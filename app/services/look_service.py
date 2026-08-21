"""
Lógica de negócio do "look do dia".

Orquestra a composição determinística (services/ai/look_generation), resolve as
peças sugeridas para os dados que o frontend precisa renderizar, persiste o
registro em `looks_history` e devolve a resposta pronta.

Sem IA paga e sem LLM: a composição é 100% baseada em regras e nos atributos já
salvos em `clothing_items`.
"""

from __future__ import annotations

import uuid

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


class LookNotAvailableError(Exception):
    """Erro de negócio (reservado para indisponibilidade genérica da geração)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


def generate_look(
    db: Session,
    *,
    user_id: uuid.UUID,
    payload: GenerateLookRequest,
) -> GenerateLookResponse:
    """
    Gera o look do dia, persiste em `looks_history` e devolve a resposta.

    Degrada graciosamente: se o guarda-roupa for insuficiente, a resposta vem
    com `looks` vazio (ou reduzido) e uma `note` explicativa — nunca um erro.
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
    db.add(history)
    db.commit()

    return GenerateLookResponse(
        condicoes_climaticas=payload.condicoes_climaticas,
        ocasiao=payload.ocasiao,
        temperatura_min=payload.temperatura_min,
        temperatura_max=payload.temperatura_max,
        looks=looks_public,
        note=result.get("note"),
    )
