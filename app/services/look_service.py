"""
Lógica de negócio do "look do dia".

Orquestra a composição (services/ai/look_generation — pré-filtro determinístico
mais uma chamada à API do Claude), resolve as peças sugeridas para os dados que
o frontend precisa renderizar, persiste o registro em `looks_history` e devolve
a resposta pronta.

O histórico não é só arquivo: os looks recentes voltam como CONTEXTO da próxima
geração, para a Miranda não repetir na terça a combinação que ditou na segunda.

⚠️ A composição chama uma API PAGA. A análise de peça (services/ai/clothing_analysis)
continua self-hosted e gratuita — são coisas diferentes. Ver README, seção 12.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.clothing_item import ClothingItem
from app.models.look_history import LookHistory
from app.schemas.look import (
    GenerateLookRequest,
    GenerateLookResponse,
    LookItemPublic,
    LookSuggestionPublic,
)
from app.services.ai.look_generation import generate_daily_look
from app.services.storage import authenticated_image_url
from app.services.wardrobe_service import list_items

logger = logging.getLogger("miranda.look_service")


def _item_to_payload(item: ClothingItem) -> dict:
    """Converte uma peça ORM no dict que a camada de composição consome."""
    return {
        "id": str(item.id),
        "name": item.name,
        "category": item.category.value,
        "cor_primaria": item.cor_primaria,
        "cor_secundaria": item.cor_secundaria,
        "estampa": item.estampa,
        "formalidade": item.formalidade.value if item.formalidade else None,
        "peso_termico": item.peso_termico.value if item.peso_termico else None,
        "serve_chuva": item.serve_chuva,
        "estacoes": item.estacoes,
    }


# Quantos looks recentes viram contexto da próxima geração. Cada um é token
# pago em TODA chamada seguinte, então o corte é baixo de propósito: o objetivo
# é "não repita o que você acabou de sugerir", não uma memória longa.
_RECENT_LOOKS_LIMIT = 3


def _recent_item_ids(
    db: Session, *, user_id: uuid.UUID, limit: int = _RECENT_LOOKS_LIMIT
) -> list[list[str]]:
    """
    Lê os núcleos dos looks recentes do usuário, do mais novo para o mais antigo.

    Returns:
        Uma lista de listas de ids, no máximo `limit` entradas. Registros de
        gerações que falharam não têm looks e simplesmente não contribuem.
    """
    rows = (
        db.query(LookHistory)
        .filter(LookHistory.user_id == user_id)
        .order_by(desc(LookHistory.data_gerado))
        .limit(limit)
        .all()
    )

    recent: list[list[str]] = []
    for row in rows:
        payload = row.itens_sugeridos
        if not isinstance(payload, dict):
            continue
        for look in payload.get("looks") or []:
            ids = look.get("item_ids") if isinstance(look, dict) else None
            # Um registro editado à mão (ex.: `"item_ids": 5`) não pode derrubar
            # a geração com um TypeError na hora de iterar — daí a checagem de
            # tipo antes de percorrer `ids`, não só de "verdadeiro".
            if isinstance(ids, list) and ids:
                recent.append([str(i) for i in ids])
            if len(recent) >= limit:
                return recent
    return recent


def generate_look(
    db: Session,
    *,
    user_id: uuid.UUID,
    payload: GenerateLookRequest,
) -> GenerateLookResponse:
    """
    Gera o look do dia, persiste em `looks_history` e devolve a resposta.

    Degrada graciosamente em duas situações distintas, ambas com HTTP 200:
    guarda-roupa insuficiente (nem chega a chamar a API) e API indisponível.
    Nos dois casos a resposta vem com `looks` vazio e uma `note` explicativa.
    """
    items = list_items(db, user_id=user_id)
    items_payload = [_item_to_payload(i) for i in items]
    condicoes = [c.value for c in payload.condicoes_climaticas]
    weather = {
        "temperatura_min": payload.temperatura_min,
        "temperatura_max": payload.temperatura_max,
        "condicoes": condicoes,
    }

    result = generate_daily_look(
        items_payload,  # type: ignore[arg-type]
        weather,  # type: ignore[arg-type]
        ocasiao=payload.ocasiao.value,
        recent_item_ids=_recent_item_ids(db, user_id=user_id),
    )

    # Índice id → peça ORM para resolver os dados de renderização.
    by_id: dict[str, ClothingItem] = {str(i.id): i for i in items}

    looks_public: list[LookSuggestionPublic] = []
    persisted_looks: list[dict] = []

    for suggested in result["looks"]:
        pieces_public: list[LookItemPublic] = []
        piece_ids: list[str] = []
        for entry in suggested["items"]:
            item = by_id.get(entry["item_id"])
            if item is None:
                continue
            piece_ids.append(str(item.id))
            pieces_public.append(
                LookItemPublic(
                    item_id=item.id,
                    role=entry["role"],
                    name=item.name,
                    category=item.category,
                    image_url=authenticated_image_url(item.id),
                    cor_primaria=item.cor_primaria,
                )
            )

        looks_public.append(
            LookSuggestionPublic(
                id=f"look-{suggested['label']}",
                label=suggested["label"],
                items=pieces_public,
                commentary=suggested["commentary"],
            )
        )
        persisted_looks.append(
            {
                "label": suggested["label"],
                "item_ids": piece_ids,
                "commentary": suggested["commentary"],
            }
        )

    # ── Persistência em looks_history (primeira vez que a tabela é usada) ────
    justificativa = "\n\n".join(pl["commentary"] for pl in persisted_looks)
    if result.get("note"):
        justificativa = (
            f"{justificativa}\n\n[nota] {result['note']}"
            if justificativa
            else f"[nota] {result['note']}"
        )

    history = LookHistory(
        user_id=user_id,
        temperatura_min=payload.temperatura_min,
        temperatura_max=payload.temperatura_max,
        # Condições múltiplas gravadas juntas (ver nota na coluna do modelo).
        condicao_climatica=", ".join(condicoes),
        ocasiao=payload.ocasiao.value,
        itens_sugeridos={"looks": persisted_looks, "note": result.get("note")},
        justificativa=justificativa or None,
    )
    # Persistência é auditoria, não o produto: os looks já foram compostos (e
    # pagos) quando chegamos aqui. Se o commit falhar (conexão caiu, pool
    # esgotado, uma constraint), o erro é registrado mas NUNCA pode jogar fora
    # um resultado que já saiu do bolso do dono do projeto.
    try:
        db.add(history)
        db.commit()
    except Exception:
        logger.exception("Falha ao persistir looks_history — resposta é devolvida mesmo assim.")
        db.rollback()

    return GenerateLookResponse(
        condicoes_climaticas=payload.condicoes_climaticas,
        ocasiao=payload.ocasiao,
        temperatura_min=payload.temperatura_min,
        temperatura_max=payload.temperatura_max,
        looks=looks_public,
        note=result.get("note"),
    )
