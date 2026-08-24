"""
Quota de peças por usuário.

Sem teto, uma conta pode encher o disco e a tabela — e, como cada peça pode
passar pelo FashionCLIP, custar CPU indefinidamente. 150 é generoso para um
guarda-roupa real e finito para um script.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.clothing_item import ClothingItem
from app.models.enums import ClothingCategory
from app.models.user import User


def _png(cor=(120, 60, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), cor).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def dono(db):
    u = User(
        name="Dono da Quota",
        email=f"quota-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(ClothingItem).filter(ClothingItem.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def _auth(user) -> dict:
    token = create_access_token(str(user.id), token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def _encher(db, user, quantas: int):
    """Cria peças direto no banco — mais rápido que passar pela rota."""
    for i in range(quantas):
        db.add(
            ClothingItem(
                user_id=user.id,
                name=f"peça {i}",
                category=ClothingCategory.CAMISA,
                image_path=f"seed_quota_{uuid.uuid4().hex}.png",
            )
        )
    db.commit()


def _cadastrar(client, user, nome="nova"):
    return client.post(
        "/api/wardrobe/items",
        headers=_auth(user),
        data={"name": nome, "category": "camisa"},
        files={"image": ("p.png", _png(), "image/png")},
    )


def test_abaixo_da_quota_o_upload_funciona(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 5)
    _encher(db, dono, 4)
    assert _cadastrar(client, dono).status_code == 201


def test_na_quota_o_upload_e_recusado(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 5)
    _encher(db, dono, 5)
    resp = _cadastrar(client, dono)
    assert resp.status_code == 409
    assert "5" in resp.text, "a mensagem precisa dizer qual é o teto"


def test_a_recusa_nao_cria_a_peca(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 3)
    _encher(db, dono, 3)
    _cadastrar(client, dono)
    assert db.query(ClothingItem).filter(ClothingItem.user_id == dono.id).count() == 3


def test_a_quota_e_por_usuario_e_nao_global(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 2)
    _encher(db, dono, 2)

    outro = User(
        name="Outro",
        email=f"outro-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(outro)
    db.commit()
    db.refresh(outro)
    try:
        assert _cadastrar(client, dono).status_code == 409
        assert _cadastrar(client, outro).status_code == 201
    finally:
        db.query(ClothingItem).filter(ClothingItem.user_id == outro.id).delete()
        db.delete(outro)
        db.commit()


def test_apagar_libera_uma_vaga(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 3)
    _encher(db, dono, 3)
    assert _cadastrar(client, dono).status_code == 409

    alguma = db.query(ClothingItem).filter(ClothingItem.user_id == dono.id).first()
    client.delete(f"/api/wardrobe/items/{alguma.id}", headers=_auth(dono))
    assert _cadastrar(client, dono).status_code == 201


def test_o_padrao_e_generoso_mas_finito():
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgresql+psycopg2://u:p@localhost:5432/db",
        JWT_SECRET_KEY="x" * 48,
        _env_file=None,
    )
    assert s.MAX_ITEMS_PER_USER == 150
