"""
Primitivas de segurança: hashing de senha (bcrypt) e geração/validação de JWT.

Usamos a biblioteca `bcrypt` diretamente (em vez de passlib) para evitar
problemas de compatibilidade conhecidos entre passlib e versões recentes do
bcrypt, e por ser uma dependência mais enxuta.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings


# ── Senha ─────────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """Gera o hash bcrypt de uma senha em texto puro."""
    # bcrypt opera sobre bytes e trunca em 72 bytes; codificamos em utf-8.
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha em texto puro corresponde ao hash armazenado."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        # Hash malformado no banco — trata como senha inválida em vez de estourar.
        return False


# ── JWT ───────────────────────────────────────────────────────────────

def _create_token(subject: str, expires_delta: timedelta, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,          # id do usuário (uuid em string)
        "type": token_type,      # "access" ou "password_reset"
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str) -> str:
    """Cria um JWT de acesso para o usuário informado."""
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_type="access",
    )


def create_password_reset_token(user_id: str) -> str:
    """Cria um JWT de redefinição de senha, de vida curta."""
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        token_type="password_reset",
    )


def decode_token(token: str, expected_type: str) -> str | None:
    """
    Decodifica e valida um JWT, garantindo o tipo esperado.
    Retorna o `sub` (id do usuário) em caso de sucesso, ou None se o token for
    inválido, expirado ou de tipo incorreto.
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None

    if payload.get("type") != expected_type:
        return None

    subject = payload.get("sub")
    if not isinstance(subject, str):
        return None
    return subject
