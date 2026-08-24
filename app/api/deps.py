"""
Dependencies do FastAPI compartilhadas pelas rotas.

`get_current_user` valida o JWT que vem no header `Authorization` OU no cookie
de sessão e retorna o usuário autenticado, protegendo as rotas.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.services.auth_service import get_user_from_access_token


def _extract_token(request: Request) -> str | None:
    """
    Pega o token do header `Authorization` ou do cookie de sessão, nessa ordem.

    O header ganha de propósito: é o canal explícito de quem sabe o que está
    fazendo (scripts, testes, um app nativo). Se ele veio e está errado, a
    requisição falha — cair para o cookie ali deixaria um header inválido ser
    silenciosamente ignorado, que é o tipo de comportamento que esconde bug.
    """
    header = request.headers.get("Authorization")
    if header:
        scheme, _, value = header.partition(" ")
        return value if scheme.lower() == "bearer" else None
    return request.cookies.get(settings.AUTH_COOKIE_NAME)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = _extract_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = get_user_from_access_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada. Faça login novamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
