"""
Composição do "look do dia" — pré-filtro determinístico + API do Claude.

Duas etapas, com naturezas deliberadamente diferentes:

  1. PRÉ-FILTRO (gratuito, determinístico, local). Descarta o que a OCASIÃO não
     admite (inviolável) e reduz o guarda-roupa às peças cujo peso térmico cabe
     no dia. Roda ANTES de qualquer chamada paga, por dois motivos: corta
     tokens — e portanto custo — em toda geração, e resolve com uma regra de
     três linhas uma decisão que não precisa de modelo de linguagem.

  2. COMPOSIÇÃO (API do Claude). O subconjunto filtrado, o clima, a ocasião e os
     looks recentes vão para o modelo, que decide as combinações e escreve as
     justificativas seguindo o manual de estilo da Miranda
     (`look_prompt.MIRANDA_SYSTEM_PROMPT`).

── O que mudou nesta migração ──────────────────────────────────────────────
A composição por regras de cor e formalidade e as justificativas por template
foram REMOVIDAS. Não sobraram como fallback: um motor de regras mantido só para
emergências apodrece sem ninguém perceber, e a degradação honesta ("não foi
possível gerar agora") é melhor produto que um look mediano assinado pela
Miranda. A análise de peça (FashionCLIP, k-means, regras) não foi tocada e
continua self-hosted e gratuita.

── Custo ──────────────────────────────────────────────────────────────────
Cada geração é uma chamada paga. O consumo de tokens e o custo estimado saem em
log a cada chamada (ver `claude_client`). Não há controle de quota nesta fase.

── Filosofia, inalterada ───────────────────────────────────────────────────
Degradar graciosamente. Esta função NUNCA lança: guarda-roupa insuficiente, API
fora do ar ou resposta ilegível viram `looks: []` com uma `note` que explica o
que houve. A rota devolve HTTP 200 em todos os casos.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional, TypedDict

from app.core.config import settings
from app.services.ai import claude_client
from app.services.ai.claude_client import LookApiFatal, LookApiTransient
from app.services.ai.look_prompt import (
    LOOK_LABELS,
    LOOK_RESPONSE_SCHEMA,
    MIRANDA_SYSTEM_PROMPT,
    ROLE_ACCESSORY,
    ROLE_BOTTOM,
    ROLE_DRESS,
    ROLE_FOOTWEAR,
    ROLE_OUTER,
    ROLE_TOP,
    VALID_ROLES,
    build_user_message,
)
from app.services.ai.occasions import OccasionProfile, get_profile

logger = logging.getLogger("miranda.ai.look_generation")


# ─────────────────────────────────────────────────────────────────────────────
# Tipos de entrada/saída
# ─────────────────────────────────────────────────────────────────────────────
class WeatherInfo(TypedDict):
    temperatura_min: float
    temperatura_max: float
    # Condições combináveis do dia (ex.: ["sol", "vento"]). Lista vazia é
    # tolerada e equivale a "dia sem particularidade".
    condicoes: list[str]


class SuggestedLookItem(TypedDict):
    item_id: str
    role: str


class SuggestedLook(TypedDict):
    label: str
    items: list[SuggestedLookItem]
    commentary: str


class DailyLookResult(TypedDict):
    looks: list[SuggestedLook]
    # Nota opcional: guarda-roupa limitado, filtro relaxado, ou a explicação da
    # indisponibilidade. None quando a composição foi plena e sem ressalva.
    note: Optional[str]
    # True somente quando a FALHA foi nossa (API fora do ar, chave inválida,
    # resposta ilegível). Guarda-roupa insuficiente é `False`: não é falha, é
    # uma resposta legítima sobre o acervo da pessoa. A distinção existe para o
    # log e para o histórico — o frontend renderiza `note` nos dois casos.
    unavailable: bool


class LookParseError(Exception):
    """A resposta da API não pôde ser interpretada como uma composição válida."""


# ─────────────────────────────────────────────────────────────────────────────
# Constantes de domínio — ajustáveis e documentadas
# ─────────────────────────────────────────────────────────────────────────────

# Categorias agrupadas por "posição" no look.
BOTTOMS = {"calca", "saia"}          # parte de baixo
TOPS = {"camisa", "malha"}           # parte de cima
OUTERS = {"blazer", "casaco"}        # sobreposição
DRESSES = {"vestido"}                # peça única (cobre o corpo inteiro)
FOOTWEAR = {"calcado"}               # calçado
SCARVES = {"cachecol"}               # complemento de aquecimento
ACCESSORIES = {"acessorio", "outros"}  # complemento opcional

# ── Faixas de temperatura (°C) → pesos térmicos aceitáveis ───────────────────
# A temperatura de referência dá MAIS peso à mínima ("vestir por segurança":
# é pior passar frio do que calor), por isso 0.6*min + 0.4*max.
TEMP_MIN_WEIGHT = 0.6
TEMP_MAX_WEIGHT = 0.4

COLD_MAX = 15.0   # temp_ref < 15  → frio
MILD_MAX = 25.0   # 15 <= temp_ref <= 25 → ameno ; > 25 → quente

BAND_COLD = "frio"
BAND_MILD = "ameno"
BAND_HOT = "quente"

# Pesos aceitos em cada faixa. Peça com peso NULO passa sempre (política
# permissiva): o campo fica vazio quando a análise não foi conclusiva, e cortar
# a peça por isso puniria o usuário por uma limitação nossa.
ACCEPTABLE_PESO: dict[str, set[str]] = {
    BAND_COLD: {"pesado", "medio"},
    BAND_MILD: {"medio", "leve"},
    BAND_HOT: {"leve"},
}

MAX_LOOKS = 3

# Espera entre tentativas, em segundos. Curta de propósito: há uma pessoa
# olhando a tela de carregamento. Três tentativas somam menos de 2s de espera.
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (0.6, 1.5)

_UNAVAILABLE_NOTE = (
    "A Miranda não conseguiu compor o look agora. Tente novamente em instantes."
)

# Papel → conjunto de categorias que podem legitimamente ocupá-lo. Usado para
# reconferir a saída do modelo: um "vestido" declarado como "peça de baixo"
# passaria pelo schema (o enum só valida a string) e produziria um look errado.
_ROLE_CATEGORIES: dict[str, set[str]] = {
    ROLE_BOTTOM: BOTTOMS,
    ROLE_TOP: TOPS,
    ROLE_OUTER: OUTERS,
    ROLE_DRESS: DRESSES,
    ROLE_FOOTWEAR: FOOTWEAR,
    ROLE_ACCESSORY: SCARVES | ACCESSORIES,
}


# ─────────────────────────────────────────────────────────────────────────────
# Pré-filtro (etapa 1 — gratuita)
# ─────────────────────────────────────────────────────────────────────────────
def _reference_temp(weather: WeatherInfo) -> float:
    return (
        TEMP_MIN_WEIGHT * float(weather["temperatura_min"])
        + TEMP_MAX_WEIGHT * float(weather["temperatura_max"])
    )


def _band_for(temp_ref: float) -> str:
    if temp_ref < COLD_MAX:
        return BAND_COLD
    if temp_ref <= MILD_MAX:
        return BAND_MILD
    return BAND_HOT


def _drop_forbidden(
    items: list[dict[str, Any]], profile: OccasionProfile
) -> list[dict[str, Any]]:
    """
    Remove as categorias que a ocasião NÃO ADMITE.

    Inviolável: nunca é relaxado, nem em guarda-roupa pobre. É preferível não
    montar um look de academia a sugerir um blazer para ela — e é barato demais
    para delegar ao modelo.
    """
    if not profile.forbidden_categories:
        return list(items)
    return [i for i in items if i.get("category") not in profile.forbidden_categories]


def _thermal_prefilter(items: list[dict[str, Any]], band: str) -> list[dict[str, Any]]:
    """
    Mantém peças cujo peso térmico é compatível com a faixa OU nulo (permissivo).
    Peças com peso conhecido e incompatível são removidas.
    """
    acceptable = ACCEPTABLE_PESO[band]
    return [
        it for it in items
        if it.get("peso_termico") is None or it.get("peso_termico") in acceptable
    ]


def _partition(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    slots: dict[str, list[dict[str, Any]]] = {
        "bottoms": [], "tops": [], "outers": [],
        "dresses": [], "footwear": [], "scarves": [], "accessories": [],
    }
    for it in items:
        cat = it.get("category")
        if cat in BOTTOMS:
            slots["bottoms"].append(it)
        elif cat in TOPS:
            slots["tops"].append(it)
        elif cat in OUTERS:
            slots["outers"].append(it)
        elif cat in DRESSES:
            slots["dresses"].append(it)
        elif cat in FOOTWEAR:
            slots["footwear"].append(it)
        elif cat in SCARVES:
            slots["scarves"].append(it)
        elif cat in ACCESSORIES:
            slots["accessories"].append(it)
    return slots


def _have_core(slots: dict[str, list[dict[str, Any]]]) -> bool:
    """Há núcleo possível: um vestido, ou um par baixo+cima."""
    return bool(slots["dresses"]) or (bool(slots["bottoms"]) and bool(slots["tops"]))


# ─────────────────────────────────────────────────────────────────────────────
# Interpretação da resposta (etapa 2 — validação da saída do modelo)
# ─────────────────────────────────────────────────────────────────────────────
def _structure_is_valid(
    categories: list[str], profile: OccasionProfile
) -> tuple[bool, str]:
    """
    Confere as regras estruturais que são da CASA, não do modelo.

    O manual de estilo pede tudo isto, e o modelo obedece na esmagadora maioria
    das vezes — mas "quase sempre" não é uma garantia que se possa mostrar ao
    usuário. Esta função é a garantia.

    Returns:
        (válido, motivo). O motivo entra no log quando um look é descartado.
    """
    if profile.forbidden_categories and (set(categories) & profile.forbidden_categories):
        return False, f"categoria proibida em {profile.key}"

    n_dress = sum(1 for c in categories if c in DRESSES)
    n_bottom = sum(1 for c in categories if c in BOTTOMS)
    n_top = sum(1 for c in categories if c in TOPS)
    n_outer = sum(1 for c in categories if c in OUTERS)

    if n_outer > 1:
        return False, "mais de uma sobreposição"

    if n_dress:
        if n_dress > 1:
            return False, "mais de uma peça única"
        if n_bottom or n_top:
            return False, "peça única acompanhada de peça de baixo ou de cima"
        return True, ""

    if n_bottom != 1 or n_top != 1:
        return False, f"núcleo inválido ({n_bottom} de baixo, {n_top} de cima)"
    return True, ""


def _parse_reply(
    text: str, by_id: dict[str, dict[str, Any]], profile: OccasionProfile
) -> tuple[list[SuggestedLook], Optional[str]]:
    """
    Interpreta a resposta da API e a valida contra o subconjunto que foi enviado.

    A tolerância aqui é deliberadamente estreita. `output_config.format` já faz a
    API garantir JSON bem formado no formato certo, então uma resposta que não
    passe daqui indica algo de fato errado — e a resposta certa a isso é nova
    tentativa, não adivinhação. Extrair JSON de um texto com prosa em volta
    mascararia o problema e um dia entregaria um look montado a partir de um
    fragmento.

    Args:
        text: corpo bruto devolvido pelo modelo.
        by_id: subconjunto ENVIADO, indexado por id. É contra ele que os ids da
            resposta são conferidos — um id de fora significa peça inventada.
        profile: perfil da ocasião, para reconferir as categorias proibidas.

    Returns:
        (looks válidos, nota do modelo). Os rótulos são reatribuídos por posição.

    Raises:
        LookParseError: JSON malformado, prosa fora do JSON, id inexistente,
            papel desconhecido, ou nenhum look estruturalmente válido.
    """
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError) as exc:
        raise LookParseError(f"resposta não é JSON válido: {exc}") from exc

    if not isinstance(payload, dict):
        raise LookParseError("resposta não é um objeto JSON")

    raw_looks = payload.get("looks")
    if not isinstance(raw_looks, list) or not raw_looks:
        raise LookParseError("resposta sem a lista 'looks'")

    note = payload.get("note")
    if note is not None and not isinstance(note, str):
        note = None

    looks: list[SuggestedLook] = []
    for raw in raw_looks[:MAX_LOOKS]:
        if not isinstance(raw, dict):
            raise LookParseError("entrada de look que não é um objeto")

        raw_items = raw.get("items")
        commentary = raw.get("commentary")
        if not isinstance(raw_items, list) or not raw_items:
            raise LookParseError("look sem peças")
        if not isinstance(commentary, str) or not commentary.strip():
            raise LookParseError("look sem justificativa")

        items: list[SuggestedLookItem] = []
        categories: list[str] = []
        seen: set[str] = set()
        duplicated = False

        for entry in raw_items:
            if not isinstance(entry, dict):
                raise LookParseError("peça que não é um objeto")
            item_id = entry.get("item_id")
            role = entry.get("role")

            if not isinstance(item_id, str) or item_id not in by_id:
                raise LookParseError(f"id de peça fora do subconjunto: {item_id!r}")
            if role not in VALID_ROLES:
                raise LookParseError(f"papel desconhecido: {role!r}")

            category = str(by_id[item_id].get("category"))
            if category not in _ROLE_CATEGORIES[role]:
                raise LookParseError(
                    f"peça de categoria {category!r} declarada como {role!r}"
                )

            if item_id in seen:
                duplicated = True
                break
            seen.add(item_id)

            items.append(SuggestedLookItem(item_id=item_id, role=role))
            categories.append(category)

        if duplicated:
            logger.warning("Look descartado: peça repetida dentro do mesmo look.")
            continue

        ok, reason = _structure_is_valid(categories, profile)
        if not ok:
            logger.warning("Look descartado (%s) — categorias: %s", reason, categories)
            continue

        looks.append(
            SuggestedLook(
                # O rótulo do modelo é conferido, não confiado: dois "I" ou um
                # salto para "III" sairiam errados na tela. A posição manda.
                label=LOOK_LABELS[len(looks)],
                items=items,
                commentary=commentary.strip(),
            )
        )

    if not looks:
        raise LookParseError("nenhum look estruturalmente válido na resposta")

    return looks, note


# ─────────────────────────────────────────────────────────────────────────────
# Ponto de entrada
# ─────────────────────────────────────────────────────────────────────────────
def generate_daily_look(
    items: list[dict[str, Any]],
    weather: WeatherInfo,
    ocasiao: Optional[str] = None,
    recent_item_ids: Optional[list[list[str]]] = None,
) -> DailyLookResult:
    """
    Gera de 2 a 3 sugestões de look para o dia.

    Args:
        items: peças do usuário (cada uma como dict com id e atributos de moda).
        weather: mínima, máxima e a LISTA de condições climáticas do dia.
        ocasiao: para o que a pessoa precisa do look (chave de `Ocasiao`).
            Ausente ou desconhecida cai em `dia_a_dia`, o registro mais elástico.
        recent_item_ids: núcleos dos looks recentes, para o modelo não repetir a
            combinação que acabou de sugerir. Cada entrada é uma lista de ids.

    Returns:
        DailyLookResult. NUNCA lança: toda falha vira `looks: []` mais uma
        `note` explicativa, e `unavailable=True` quando a falha foi nossa.
    """
    profile = get_profile(ocasiao)
    band = _band_for(_reference_temp(weather))
    notes: list[str] = []

    # ── Etapa 1: pré-filtro (gratuito) ──────────────────────────────────────
    allowed = _drop_forbidden(items, profile)
    n_forbidden = len(items) - len(allowed)
    thermal = _thermal_prefilter(allowed, band)

    # Uma única relaxação: se o corte térmico não deixa nem um núcleo possível,
    # é melhor vestir a pessoa com o que ela tem e avisar do descompasso do que
    # devolver a tela vazia. A proibição da ocasião NÃO participa disso.
    if _have_core(_partition(thermal)):
        selection = thermal
    elif _have_core(_partition(allowed)):
        selection = allowed
        notes.append(
            "Poucas peças combinam com esta temperatura; ampliei a seleção para "
            "conseguir compor."
        )
    else:
        if n_forbidden:
            note = (
                f"Nenhuma peça do guarda-roupa serve para {profile.phrase}: "
                f"{n_forbidden} peça(s) foram descartadas por não caberem nesta "
                "ocasião. Cadastre uma parte de baixo e uma de cima adequadas."
            )
        else:
            note = (
                "Ainda não há peças suficientes para compor um look completo. "
                "Cadastre ao menos uma parte de baixo e uma de cima — ou um "
                "vestido — para a Miranda trabalhar."
            )
        # Guarda-roupa insuficiente NÃO é indisponibilidade: é uma resposta
        # legítima sobre o acervo. E não gasta uma chamada paga.
        return DailyLookResult(looks=[], note=note, unavailable=False)

    # ── Etapa 2: composição (chamada paga) ──────────────────────────────────
    by_id = {str(p["id"]): p for p in selection}
    user_message = build_user_message(
        selection, dict(weather), profile.label, recent_item_ids or []
    )

    max_attempts = max(1, settings.ANTHROPIC_MAX_ATTEMPTS)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            reply = claude_client.request_composition(
                MIRANDA_SYSTEM_PROMPT, user_message, LOOK_RESPONSE_SCHEMA
            )
            looks, model_note = _parse_reply(reply.text, by_id, profile)

        except LookApiFatal as exc:
            # Chave inválida ou requisição recusada não melhoram na tentativa
            # seguinte — e cada tentativa custa. Desiste na hora.
            logger.error("Composição indisponível (falha definitiva): %s", exc)
            return DailyLookResult(
                looks=[], note=_UNAVAILABLE_NOTE, unavailable=True
            )

        except (LookApiTransient, LookParseError) as exc:
            # Interpretação falha entra no mesmo laço que o 429 de propósito:
            # uma resposta ilegível é tão retentável quanto uma rede instável, e
            # separá-las daria dois laços com a mesma forma.
            last_error = exc
            logger.warning(
                "Tentativa %d/%d de composição falhou: %s", attempt, max_attempts, exc
            )
            if attempt < max_attempts:
                time.sleep(
                    RETRY_BACKOFF_SECONDS[
                        min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                    ]
                )
            continue

        except Exception as exc:  # noqa: BLE001
            # Rede de segurança final. O contrato da rota é HTTP 200 sempre, e
            # um erro que ninguém previu não pode ser o que quebra isso.
            logger.exception("Erro inesperado ao compor o look: %s", exc)
            return DailyLookResult(
                looks=[], note=_UNAVAILABLE_NOTE, unavailable=True
            )

        if model_note:
            notes.append(model_note)
        return DailyLookResult(
            looks=looks, note=" ".join(notes) if notes else None, unavailable=False
        )

    logger.error(
        "Composição indisponível após %d tentativas. Última falha: %s",
        max_attempts,
        last_error,
    )
    return DailyLookResult(looks=[], note=_UNAVAILABLE_NOTE, unavailable=True)
