"""
Lógica de negócio de autenticação, separada das rotas HTTP.

As funções aqui levantam `AuthError` com uma mensagem e um status HTTP
sugerido; a camada de rotas converte isso em `HTTPException`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_password_reset_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User


class AuthError(Exception):
    """Erro de autenticação com status HTTP associado."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def register_user(db: Session, *, name: str, email: str, password: str) -> User:
    """Cria um novo usuário, rejeitando e-mail duplicado."""
    email = email.lower()
    if _get_user_by_email(db, email) is not None:
        raise AuthError(409, "Este e-mail já está cadastrado.")

    user = User(
        name=name.strip(),
        email=email,
        hashed_password=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, *, email: str, password: str) -> str:
    """Valida credenciais e retorna um JWT de acesso."""
    user = _get_user_by_email(db, email)
    # Mensagem genérica de propósito: não revela se o e-mail existe.
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthError(401, "E-mail ou senha inválidos.")
    return create_access_token(str(user.id))


def get_user_from_access_token(db: Session, token: str) -> User | None:
    """Resolve o usuário a partir de um JWT de acesso válido."""
    user_id = decode_token(token, expected_type="access")
    if user_id is None:
        return None
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return None
    return db.get(User, uid)


def create_reset_token_for_email(db: Session, *, email: str) -> str | None:
    """
    Gera um token de redefinição de senha para o e-mail informado.

    Retorna o token se o usuário existir, ou None caso contrário. A rota que
    chama esta função sempre responde de forma genérica, para não revelar quais
    e-mails estão cadastrados.
    """
    user = _get_user_by_email(db, email)
    if user is None:
        return None
    return create_password_reset_token(str(user.id))


def reset_password(db: Session, *, token: str, new_password: str) -> None:
    """Troca a senha do usuário associado a um token de redefinição válido."""
    user_id = decode_token(token, expected_type="password_reset")
    if user_id is None:
        raise AuthError(400, "Token de redefinição inválido ou expirado.")
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise AuthError(400, "Token de redefinição inválido ou expirado.")

    user = db.get(User, uid)
    if user is None:
        raise AuthError(400, "Token de redefinição inválido ou expirado.")

    user.hashed_password = hash_password(new_password)
    db.add(user)
    db.commit()
