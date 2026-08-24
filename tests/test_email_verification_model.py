"""
Modelo da verificação de e-mail.

Roda contra o Postgres de DATABASE_URL (mesmo padrão de
tests/test_wardrobe_image_access.py). Sem banco acessível, os testes são PULADOS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal, engine
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User


@pytest.fixture(scope="module")
def db():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db) -> User:
    u = User(
        name="Quem Verifica",
        email=f"verif-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == u.id
    ).delete()
    db.delete(u)
    db.commit()


def test_conta_nova_comeca_sem_verificacao(db, user):
    assert user.email_verified_at is None
    assert user.is_email_verified is False


def test_carimbar_o_horario_vira_a_propriedade(db, user):
    user.email_verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    assert user.is_email_verified is True


def test_o_token_de_verificacao_guarda_so_o_hash(db, user):
    """
    Mesmo desenho do token de reset: o valor em claro nunca é persistido, então
    um vazamento do banco não entrega tokens utilizáveis.
    """
    token = EmailVerificationToken(
        user_id=user.id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(token)
    db.commit()
    db.refresh(token)

    assert token.used_at is None
    assert len(token.token_hash) == 64
    assert not hasattr(token, "token")  # nada em claro no modelo


def test_os_tokens_morrem_com_o_usuario(db):
    """ON DELETE CASCADE: apagar a conta não pode deixar token órfão válido."""
    u = User(
        name="Efêmera",
        email=f"efemera-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(
        EmailVerificationToken(
            user_id=u.id,
            token_hash="b" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
    )
    db.commit()
    uid = u.id
    db.delete(u)
    db.commit()

    restantes = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.user_id == uid)
        .count()
    )
    assert restantes == 0
