"""
Transporte da composição de look: uma chamada à API do Claude, um resultado.

Este módulo não sabe nada sobre moda e NÃO tenta de novo. Ele faz uma chamada,
classifica o que deu errado e registra quanto custou. O laço de retry vive em
`look_generation`, onde ele também cobre a falha de INTERPRETAÇÃO da resposta —
que é tão retentável quanto um 429 e não faria sentido tratar noutro lugar.

── Por que a classificação do erro é a regra central daqui ─────────────────
Errar nela custa dos dois lados: retentar um 400 três vezes é dinheiro jogado
fora numa chamada que nunca vai passar; desistir de um 429 na primeira é
indisponibilidade gratuita. Por isso a separação entre `LookApiTransient` e
`LookApiFatal` é explícita e testada caso a caso, em vez de um `except Exception`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import anthropic

from app.core.config import settings

logger = logging.getLogger("miranda.ai.claude_client")


# ── Erros ───────────────────────────────────────────────────────────────────
class LookApiError(Exception):
    """Falha ao falar com a API do Claude."""


class LookApiTransient(LookApiError):
    """Falha que pode passar numa nova tentativa (rede, rate limit, 5xx)."""


class LookApiFatal(LookApiError):
    """Falha que NÃO passa em nova tentativa (chave inválida, modelo inexistente)."""


# ── Preços ──────────────────────────────────────────────────────────────────
# US$ por milhão de tokens: (entrada, saída). Alimentam apenas o log de custo
# estimado — nenhuma decisão do sistema depende deles.
#
# ⚠️ Ao trocar ANTHROPIC_MODEL para um modelo fora desta tabela, o custo passa a
# ser registrado como 0.0 e o log avisa. Acrescente a linha em vez de confiar no
# silêncio.
MODEL_PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


@dataclass(frozen=True)
class ClaudeUsage:
    """Consumo de uma chamada. Existe para o dono do projeto acompanhar custo."""

    input_tokens: int
    output_tokens: int
    model: str

    @property
    def estimated_cost_usd(self) -> float:
        prices = MODEL_PRICES_USD_PER_MTOK.get(self.model)
        if prices is None:
            return 0.0
        price_in, price_out = prices
        return (
            self.input_tokens / 1_000_000 * price_in
            + self.output_tokens / 1_000_000 * price_out
        )


@dataclass(frozen=True)
class ClaudeReply:
    """Texto bruto devolvido pelo modelo, ainda não interpretado."""

    text: str
    usage: ClaudeUsage


# ── Cliente ─────────────────────────────────────────────────────────────────
_client: Optional[anthropic.Anthropic] = None


def _build_client() -> anthropic.Anthropic:
    """
    Constrói o cliente do SDK.

    `max_retries=0` é deliberado: o SDK tentaria 2 vezes por conta própria e,
    somado ao nosso laço de 3, daria até 9 chamadas por geração — um "loop
    agressivo" que o usuário esperando na tela pagaria em latência. As tentativas
    ficam num lugar só, visível e testável, em `look_generation`.
    """
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        max_retries=0,
        timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
    )


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = _build_client()
    return _client


def reset_client_cache() -> None:
    """Descarta o cliente memoizado. Usado por testes; não chame em produção."""
    global _client
    _client = None


def request_composition(
    system: str, user_message: str, schema: dict[str, Any]
) -> ClaudeReply:
    """
    Faz UMA chamada de composição de look. Não tenta de novo.

    Args:
        system: o manual de estilo (ver `look_prompt.MIRANDA_SYSTEM_PROMPT`).
        user_message: guarda-roupa filtrado, clima, ocasião e histórico recente.
        schema: JSON schema da resposta (ver `look_prompt.LOOK_RESPONSE_SCHEMA`).

    Returns:
        ClaudeReply com o texto bruto e o consumo da chamada.

    Raises:
        LookApiFatal: chave ausente/inválida, requisição malformada, modelo
            inexistente. Nova tentativa não ajuda.
        LookApiTransient: rede, timeout, rate limit, 5xx, resposta sem texto.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise LookApiFatal(
            "ANTHROPIC_API_KEY não está configurada. Defina-a no .env "
            "(ver .env.example) para habilitar a geração de look."
        )

    model = settings.ANTHROPIC_MODEL

    try:
        response = _get_client().messages.create(
            model=model,
            max_tokens=settings.ANTHROPIC_MAX_OUTPUT_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            # `effort` no lugar de `temperature`: os modelos atuais REJEITAM
            # `temperature` com HTTP 400. `format` faz a própria API garantir
            # JSON bem formado no formato certo — o que torna resposta
            # ilegível rara, não impossível (ver `_parse_reply`).
            output_config={
                "effort": settings.ANTHROPIC_EFFORT,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise LookApiFatal(f"Credencial recusada pela API: {exc}") from exc
    except anthropic.NotFoundError as exc:
        raise LookApiFatal(f"Modelo '{model}' não encontrado: {exc}") from exc
    except anthropic.BadRequestError as exc:
        raise LookApiFatal(f"Requisição recusada pela API: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LookApiTransient(f"Rate limit da API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        # NOTA DE IMPLEMENTAÇÃO: o SDK 1.0.0 só entrega as subclasses tipadas
        # (RateLimitError, NotFoundError, ...) quando ELE PRÓPRIO monta o erro a
        # partir de uma resposta HTTP real — `anthropic.APIStatusError(...)`
        # construído à mão (como nos dublês de teste) É a classe genérica, não
        # uma subclasse, mesmo com status_code=429. Por isso o 429 é checado aqui
        # explicitamente, e não apenas via `except anthropic.RateLimitError`
        # acima: sem isso, um 429 que chegasse como `APIStatusError` genérico
        # cairia no `else` e viraria `LookApiFatal` por engano.
        if exc.status_code == 429 or exc.status_code >= 500:
            raise LookApiTransient(f"Erro do servidor ({exc.status_code}): {exc}") from exc
        raise LookApiFatal(f"Erro da API ({exc.status_code}): {exc}") from exc
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
        raise LookApiTransient(f"Falha de rede ao chamar a API: {exc}") from exc

    usage = ClaudeUsage(
        input_tokens=getattr(response.usage, "input_tokens", 0),
        output_tokens=getattr(response.usage, "output_tokens", 0),
        model=model,
    )

    # Log de custo: é o único instrumento de acompanhamento nesta fase — não há
    # controle de quota (isso é uma fase à parte do projeto).
    cost = usage.estimated_cost_usd
    logger.info(
        "composição de look — modelo=%s input_tokens=%d output_tokens=%d "
        "custo_estimado_usd=%.6f",
        model,
        usage.input_tokens,
        usage.output_tokens,
        cost,
    )
    if cost == 0.0 and model not in MODEL_PRICES_USD_PER_MTOK:
        logger.warning(
            "Modelo '%s' não está na tabela de preços de claude_client; o custo "
            "estimado será sempre 0.0 até alguém acrescentá-lo.",
            model,
        )

    text = next(
        (b.text for b in response.content if getattr(b, "type", None) == "text"), ""
    )
    if not text.strip():
        raise LookApiTransient(
            f"A API respondeu sem texto (stop_reason="
            f"{getattr(response, 'stop_reason', '?')})."
        )

    return ClaudeReply(text=text, usage=usage)
