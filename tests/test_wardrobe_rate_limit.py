"""
Rate limit nas rotas caras de guarda-roupa.

Upload grava arquivo no disco; /analyze roda o FashionCLIP em CPU. As duas são
os caminhos mais caros da API e as únicas que um script consegue disparar em
rajada com uma conta válida.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core import rate_limit
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.clothing_item import ClothingItem
from app.models.user import User


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (10, 20, 30)).save(buf, format="PNG")
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


@pytest.fixture(autouse=True)
def _limite_baixo_e_contador_limpo(monkeypatch):
    """
    Baixa o teto para 3 e zera os contadores entre testes.

    Sem o reset, o primeiro teste consumiria a cota dos seguintes e a suíte
    passaria a depender da ordem de execução.
    """
    monkeypatch.setattr(settings, "WARDROBE_UPLOAD_RATE_LIMIT", "3/hour")
    monkeypatch.setattr(settings, "ANALYZE_RATE_LIMIT", "3/hour")
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def usuario(db):
    u = User(
        name="Quem Sobe",
        email=f"rl-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(ClothingItem).filter(ClothingItem.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def _auth(u) -> dict:
    token = create_access_token(str(u.id), token_version=u.token_version)
    return {"Authorization": f"Bearer {token}"}


def _sobe(client, u, i=0):
    return client.post(
        "/api/wardrobe/items",
        headers=_auth(u),
        data={"name": f"peça {i}", "category": "camisa"},
        files={"image": ("p.png", _png(), "image/png")},
    )


def test_uso_normal_nao_e_incomodado(client, usuario):
    """Três uploads seguidos, dentro do teto, passam."""
    for i in range(3):
        assert _sobe(client, usuario, i).status_code == 201


def test_rajada_alem_do_teto_e_recusada(client, usuario):
    for i in range(3):
        _sobe(client, usuario, i)
    excedente = _sobe(client, usuario, 99)
    assert excedente.status_code == 429


def test_o_teto_e_por_usuario_e_nao_por_ip(client, db, usuario):
    """
    Uma casa inteira atrás do mesmo NAT compartilha IP. Se a cota fosse por IP,
    uma pessoa abusando derrubaria as outras junto.
    """
    for i in range(3):
        _sobe(client, usuario, i)
    assert _sobe(client, usuario, 99).status_code == 429

    vizinho = User(
        name="Vizinho",
        email=f"viz-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(vizinho)
    db.commit()
    db.refresh(vizinho)
    try:
        # Mesmo cliente, mesmo IP, outro usuário: cota própria.
        assert _sobe(client, vizinho, 0).status_code == 201
    finally:
        db.query(ClothingItem).filter(ClothingItem.user_id == vizinho.id).delete()
        db.delete(vizinho)
        db.commit()


def test_o_analyze_tem_teto_proprio(client, usuario):
    """
    /analyze não compartilha cota com o upload: são custos diferentes e um não
    deve consumir o outro.
    """
    for _ in range(3):
        client.post(
            "/api/wardrobe/items/analyze",
            headers=_auth(usuario),
            files={"image": ("p.png", _png(), "image/png")},
        )
    excedente = client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(usuario),
        files={"image": ("p.png", _png(), "image/png")},
    )
    assert excedente.status_code == 429


def test_os_padroes_sao_generosos_para_uma_pessoa():
    """
    O teto existe contra script, não contra gente. Cadastrar 60 peças numa hora
    já é muito mais do que qualquer pessoa faz.
    """
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgresql+psycopg2://u:p@localhost:5432/db",
        JWT_SECRET_KEY="x" * 48,
        _env_file=None,
    )
    assert s.WARDROBE_UPLOAD_RATE_LIMIT == "60/hour"
    assert s.ANALYZE_RATE_LIMIT == "40/hour"


# ── Achado A3: o PUT aceitava upload sem teto de frequência ─────────────────
# O POST tinha o decorator, o PUT não. Como o PUT também recebe imagem, grava no
# disco e apaga o arquivo antigo, o teto do POST era contornável em laço via PUT
# sobre uma peça existente — e a quota de 150 peças não protege aqui, porque
# nenhuma peça é criada.
def _edita(client, u, item_id, i=0):
    return client.put(
        f"/api/wardrobe/items/{item_id}",
        headers=_auth(u),
        data={"name": f"editada {i}", "category": "camisa"},
        files={"image": ("p.png", _png(), "image/png")},
    )


def test_o_put_tambem_tem_teto(client, usuario):
    criada = _sobe(client, usuario, 0)
    assert criada.status_code == 201
    item_id = criada.json()["id"]

    # O slowapi conta por ENDPOINT: o POST acima não consome a cota do PUT.
    # Três edições cabem no teto de 3/hora; a quarta é recusada.
    assert _edita(client, usuario, item_id, 1).status_code == 200
    assert _edita(client, usuario, item_id, 2).status_code == 200
    assert _edita(client, usuario, item_id, 3).status_code == 200
    assert _edita(client, usuario, item_id, 4).status_code == 429
