"""
Rotas do "look do dia".

A composição é determinística (services/ai/look_generation) — sem LLM nem API
paga. A rota chama a geração, persiste o registro e devolve os looks. Quando o
guarda-roupa é insuficiente, a resposta vem com `looks` vazio/reduzido e uma
`note` explicativa (nunca um erro).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.schemas.look import GenerateLookRequest, GenerateLookResponse
from app.services import look_service

router = APIRouter(prefix="/looks", tags=["looks"])


@router.post("/generate", response_model=GenerateLookResponse)
def generate_look(
    payload: GenerateLookRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerateLookResponse:
    """Gera o look do dia a partir do clima informado e do guarda-roupa do usuário."""
    return look_service.generate_look(
        db, user_id=current_user.id, payload=payload
    )
