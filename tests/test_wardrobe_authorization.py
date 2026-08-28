"""
Peça de outro usuário responde 404 — nas três rotas (achado A13).

A rota de imagem já tinha esse teste. `GET /{id}`, `PUT /{id}` e `DELETE /{id}`
não tinham nenhum: as três herdam a proteção de `wardrobe_service.get_item`,
que é a mesma função exercitada pela rota de imagem, então o comportamento
*provavelmente* estava certo. Mas provavelmente não é cobertura — e a proteção
aqui é declarada por rota, não no `include_router`, então uma rota nova nasce
desprotegida se o autor esquecer.

Por que 404 e não 403: 403 confirmaria que a peça existe, transformando a rota
num oráculo de ids. Id inexistente e id de outro dono devolvem exatamente a
mesma resposta.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.clothing_item import ClothingItem
from app.models.user import User
from app.services.storage import image_storage


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (60, 110, 90)).save(buf, format="PNG")
    return buf.getvalue()


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


def _novo_usuario(db) -> User:
    u = User(
        name="Usuária",
        email=f"autz-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture
def dona(db):
    u = _novo_usuario(db)
    yield u
    db.delete(u)  # cascade remove as peças
    db.commit()


@pytest.fixture
def intrusa(db):
    u = _novo_usuario(db)
    yield u
    db.delete(u)
    db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture
def peca(client, dona, db):
    r = client.post(
        "/api/wardrobe/items",
        headers=_auth(dona),
        data={"name": "Saia de teste", "category": "saia"},
        files={"image": ("saia.png", _png(), "image/png")},
    )
    assert r.status_code == 201, r.text
    item = r.json()
    yield item

    registro = db.get(ClothingItem, uuid.UUID(item["id"]))
    if registro is not None:
        image_storage.delete(registro.image_path)


# ── A peça de outra pessoa não existe, do ponto de vista de quem pergunta ──

def test_get_de_peca_alheia_devolve_404(client, intrusa, peca):
    r = client.get(f"/api/wardrobe/items/{peca['id']}", headers=_auth(intrusa))
    assert r.status_code == 404


def test_put_em_peca_alheia_devolve_404(client, intrusa, peca):
    r = client.put(
        f"/api/wardrobe/items/{peca['id']}",
        headers=_auth(intrusa),
        data={"name": "Sequestrada", "category": "saia"},
    )
    assert r.status_code == 404


def test_delete_de_peca_alheia_devolve_404(client, intrusa, peca):
    r = client.delete(f"/api/wardrobe/items/{peca['id']}", headers=_auth(intrusa))
    assert r.status_code == 404


def test_o_put_recusado_nao_altera_a_peca(client, dona, intrusa, peca):
    # 404 não pode ser só o status: a tentativa não pode ter efeito colateral.
    client.put(
        f"/api/wardrobe/items/{peca['id']}",
        headers=_auth(intrusa),
        data={"name": "Sequestrada", "category": "saia"},
    )
    depois = client.get(f"/api/wardrobe/items/{peca['id']}", headers=_auth(dona))
    assert depois.status_code == 200
    assert depois.json()["name"] == "Saia de teste"


def test_o_delete_recusado_nao_apaga_a_peca(client, dona, intrusa, peca):
    client.delete(f"/api/wardrobe/items/{peca['id']}", headers=_auth(intrusa))
    depois = client.get(f"/api/wardrobe/items/{peca['id']}", headers=_auth(dona))
    assert depois.status_code == 200


def test_id_inexistente_e_peca_alheia_sao_indistinguiveis(client, intrusa, peca):
    # Se as duas respostas divergissem — status, corpo ou mensagem — a rota
    # viraria um oráculo: daria para varrer ids e descobrir quais existem.
    alheia = client.get(f"/api/wardrobe/items/{peca['id']}", headers=_auth(intrusa))
    inexistente = client.get(
        f"/api/wardrobe/items/{uuid.uuid4()}", headers=_auth(intrusa)
    )
    assert alheia.status_code == inexistente.status_code == 404
    assert alheia.json() == inexistente.json()


def test_a_dona_continua_alcancando_a_propria_peca(client, dona, peca):
    # A trava não pode ser tão apertada que quebre o caso legítimo.
    r = client.get(f"/api/wardrobe/items/{peca['id']}", headers=_auth(dona))
    assert r.status_code == 200
    assert r.json()["id"] == peca["id"]
