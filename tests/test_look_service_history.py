"""
O histórico recente vira contexto da próxima geração.

Roda contra o Postgres de DATABASE_URL — é `looks_history` que está sob teste, e
ela vive no banco. Sem banco acessível, os testes são PULADOS (mesmo padrão de
tests/test_wardrobe_image_access.py).
"""

from __future__ import annotations

import uuid

import pytest

from app.core.database import SessionLocal, engine
from app.models.look_history import LookHistory
from app.models.user import User
from app.services.look_service import _recent_item_ids


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
        name="Dona do Histórico",
        email=f"hist-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(LookHistory).filter(LookHistory.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def _record(db, user, looks):
    db.add(LookHistory(
        user_id=user.id,
        temperatura_min=15.0,
        temperatura_max=25.0,
        condicao_climatica="sol",
        ocasiao="dia_a_dia",
        itens_sugeridos={"looks": looks, "note": None},
        justificativa="x",
    ))
    db.commit()


def test_no_history_yields_an_empty_context(db, user):
    assert _recent_item_ids(db, user_id=user.id) == []


def test_recent_looks_are_returned_as_id_lists(db, user):
    _record(db, user, [{"label": "I", "item_ids": ["a", "b"], "commentary": "c"}])
    assert _recent_item_ids(db, user_id=user.id) == [["a", "b"]]


def test_the_context_is_capped_so_the_prompt_does_not_grow_forever(db, user):
    """
    Cada look no histórico é token pago em TODA geração seguinte. O corte existe
    para o custo por chamada não crescer com o tempo de uso do produto.
    """
    for i in range(5):
        _record(db, user, [{"label": "I", "item_ids": [f"x{i}"], "commentary": "c"}])
    assert len(_recent_item_ids(db, user_id=user.id, limit=3)) == 3


def test_a_failed_generation_contributes_nothing(db, user):
    """Registro sem looks (API indisponível) não polui o contexto."""
    _record(db, user, [])
    assert _recent_item_ids(db, user_id=user.id) == []


def test_another_users_history_is_never_leaked(db, user):
    other = User(
        name="Outra",
        email=f"outra-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(other)
    db.commit()
    db.refresh(other)
    try:
        _record(db, other, [{"label": "I", "item_ids": ["segredo"], "commentary": "c"}])
        assert _recent_item_ids(db, user_id=user.id) == []
    finally:
        db.query(LookHistory).filter(LookHistory.user_id == other.id).delete()
        db.delete(other)
        db.commit()
