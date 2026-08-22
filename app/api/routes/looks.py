"""
Rotas do "look do dia".

A composição usa a API do Claude (services/ai/look_generation), precedida de um
pré-filtro determinístico e gratuito por clima e ocasião. A rota chama a
geração, persiste o registro e devolve os looks.

Nunca devolve erro por falta de peças nem por indisponibilidade da API: nesses
casos a resposta vem com `looks` vazio e uma `note` explicativa, em HTTP 200.
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
