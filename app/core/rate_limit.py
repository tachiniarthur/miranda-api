"""
Rate limiting dos endpoints de autenticação.

Nas rotas de auth, limitar tentativas não é uma questão de disponibilidade e sim
de controle de acesso: sem teto, `login` permite força bruta de senha,
`reset-password` permite força bruta do token de redefinição, e
`forgot-password` permite varrer a base atrás de e-mails cadastrados.

A chave do limite combina IP e e-mail alvo, de propósito:

  - só por IP    → um atacante atrás de NAT/proxy compartilhado derrubaria
                   usuários legítimos junto, e trocar de IP contornaria o limite
                   para uma mesma conta;
  - só por e-mail→ um atacante enumeraria contas distintas sem nunca esbarrar no
                   teto;
  - combinado    → cada par (IP, conta) tem sua própria cota, então nem o
                   atacante consegue insistir numa conta, nem um usuário
                   legítimo é punido pelo excesso de um vizinho de rede.

`reset-password` não carrega e-mail no corpo (só o token opaco). Ali a cota cai
para "por IP", que é exatamente o eixo certo: o que se quer limitar é o número
de palpites de token que uma origem consegue dar.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import settings

logger = logging.getLogger("miranda.rate_limit")

# 5 tentativas a cada 15 minutos, por par (IP, e-mail).
AUTH_RATE_LIMIT = "5/15 minutes"

# Nome do atributo em `request.state` onde a dependency abaixo deposita o e-mail
# alvo, para que a key_func (que é síncrona e só recebe o Request) o encontre.
_EMAIL_STATE_ATTR = "auth_rate_limit_email"


def auth_rate_limit_key(request: Request) -> str:
    """
    Identidade a limitar: "<ip>|<email>".

    A key_func do slowapi é chamada de forma síncrona e recebe apenas o Request,
    então ela não pode ler o corpo da requisição. Quem faz isso é a dependency
    `stash_auth_identity`, que roda antes do endpoint e guarda o e-mail em
    `request.state`.
    """
    ip = get_remote_address(request) or "sem-ip"
    email = getattr(request.state, _EMAIL_STATE_ATTR, None) or "-"
    return f"{ip}|{email}"


async def stash_auth_identity(request: Request) -> None:
    """
    Dependency que extrai o e-mail do corpo e o guarda em `request.state`.

    Roda antes do endpoint (e, portanto, antes da checagem do slowapi). O corpo
    já foi lido e cacheado pelo FastAPI neste ponto, então `request.json()` não
    consome o stream de novo. Corpo ausente ou malformado não é problema desta
    camada: a validação do Pydantic cuida disso depois, e aqui basta cair no
    limite por IP.
    """
    try:
        body = await request.json()
    except Exception:
        return

    if isinstance(body, dict):
        email = body.get("email")
        if isinstance(email, str) and email.strip():
            # Normaliza como o auth_service faz, para que "A@x.com" e "a@x.com"
            # compartilhem a mesma cota em vez de dobrarem as tentativas.
            setattr(request.state, _EMAIL_STATE_ATTR, email.strip().lower())


def resolve_storage_uri(uri: str | None = None) -> str:
    """
    Devolve a URI de storage a usar, confirmando que o Redis responde.

    A confirmação acontece UMA vez, no import. Sem ela, um Redis fora do ar só
    apareceria na primeira requisição de auth — como erro, não como aviso — e o
    modo de falha seria HTTP 500 numa rota de login em vez de um limite mais
    frouxo.
    """
    uri = uri if uri is not None else settings.RATE_LIMIT_STORAGE_URI
    if not uri.startswith("redis://") and not uri.startswith("rediss://"):
        return uri

    try:
        import redis

        redis.Redis.from_url(uri, socket_connect_timeout=1).ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Redis inacessível em %s (%s). O rate limit cai para memory:// — "
            "correto com UM worker apenas. Suba o Redis com "
            "`docker compose up -d`.",
            uri,
            exc,
        )
        return "memory://"
    return uri


limiter = Limiter(
    key_func=auth_rate_limit_key,
    # `moving-window` conta as tentativas dos últimos 15 minutos corridos. A
    # alternativa (`fixed-window`) zera o contador em fronteiras fixas de
    # relógio, o que permitiria 10 tentativas seguidas em cima da virada.
    strategy="moving-window",
    # Emite Retry-After e X-RateLimit-* para o cliente saber quando reter.
    headers_enabled=True,
    # Contadores fora do processo (Redis por padrão), para que a cota seja a
    # mesma para todos os workers. Redis fora do ar degrada para memória com
    # aviso — ver `resolve_storage_uri`.
    storage_uri=resolve_storage_uri(),
)


def rate_limit_exceeded_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """
    Resposta 429 no mesmo formato de erro do resto da API (`detail`).

    A mensagem é deliberadamente genérica: não confirma se o e-mail existe nem
    quantas tentativas restavam — isso reabriria, pela porta do rate limit, a
    enumeração de contas que as rotas de auth fecham.
    """
    response = JSONResponse(
        status_code=429,
        content={
            "detail": (
                "Muitas tentativas. Aguarde alguns minutos antes de tentar "
                "novamente."
            )
        },
    )
    view_rate_limit = getattr(request.state, "view_rate_limit", None)
    if view_rate_limit is not None:
        # Preenche Retry-After e X-RateLimit-*; é o mesmo caminho usado pelo
        # handler padrão do slowapi.
        response = request.app.state.limiter._inject_headers(
            response, view_rate_limit
        )
    return response
