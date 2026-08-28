"""
Schemas de peça: criação e atualização têm que valer as mesmas regras.

`ClothingItemUpdate` não herdava de `ClothingItemBase` — repetia os nove campos
à mão. A duplicação em si já era ruim (ajustar um `max_length` na base deixaria
o update para trás em silêncio), mas o efeito colateral era um bug real: o
validator `_dedupe_estacoes` roda no create e NÃO rodava no update, então
estações duplicadas passavam pelo PUT (achado M20).

Roda contra o Postgres de DATABASE_URL na parte HTTP; sem banco, é pulado.
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
from app.models.user import User
from app.schemas.clothing_item import ClothingItemCreate, ClothingItemUpdate


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (90, 40, 20)).save(buf, format="PNG")
    return buf.getvalue()


# ── Nível de schema: a regra de deduplicação vale nos dois lados ────────────

def test_o_create_deduplica_estacoes():
    data = ClothingItemCreate(
        name="Camisa", category="camisa", estacoes=["verao", "verao", "inverno"]
    )
    assert [e.value for e in data.estacoes] == ["verao", "inverno"]


def test_o_update_tambem_deduplica_estacoes():
    # Este é o bug de M20: sem herdar da base, o update não tinha validator e
    # gravava a duplicata.
    data = ClothingItemUpdate(estacoes=["verao", "verao", "inverno"])
    assert [e.value for e in data.estacoes] == ["verao", "inverno"]


def test_o_update_preserva_a_ordem_de_selecao():
    data = ClothingItemUpdate(estacoes=["inverno", "verao", "inverno"])
    assert [e.value for e in data.estacoes] == ["inverno", "verao"]


def test_os_limites_de_tamanho_sao_os_mesmos_nos_dois():
    # A duplicação dos nove campos fazia os dois divergirem em silêncio no dia
    # em que alguém ajustasse um `max_length` só na base.
    for campo in ("name", "cor_primaria", "cor_secundaria", "estampa"):
        base = ClothingItemCreate.model_fields[campo]
        update = ClothingItemUpdate.model_fields[campo]
        limites_base = [c for c in base.metadata if hasattr(c, "max_length")]
        limites_update = [c for c in update.metadata if hasattr(c, "max_length")]
        assert [c.max_length for c in limites_base] == [
            c.max_length for c in limites_update
        ], f"{campo}: max_length divergiu entre create e update"


def test_no_update_todos_os_campos_sao_opcionais():
    # A semântica do update não pode mudar ao herdar: `name` e `category` são
    # obrigatórios no create e opcionais aqui.
    vazio = ClothingItemUpdate()
    assert vazio.name is None
    assert vazio.category is None


# ── Nível HTTP: a duplicata não chega ao banco ─────────────────────────────

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
def conta():
    db = SessionLocal()
    u = User(
        name="Schema",
        email=f"schema-{uuid.uuid4().hex[:8]}@exemplo.com",
        hashed_password="x" * 60,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.execute(
        __import__("sqlalchemy").text("delete from clothing_items where user_id = :u"),
        {"u": u.id},
    )
    db.query(User).filter(User.id == u.id).delete()
    db.commit()
    db.close()


def _auth(u: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(u.id), token_version=u.token_version)}"}


def test_o_put_nao_grava_estacao_duplicada(client, conta):
    criada = client.post(
        "/api/wardrobe/items",
        headers=_auth(conta),
        data={"name": "Malha", "category": "malha"},
        files={"image": ("p.png", _png(), "image/png")},
    )
    assert criada.status_code == 201
    item_id = criada.json()["id"]

    editada = client.put(
        f"/api/wardrobe/items/{item_id}",
        headers=_auth(conta),
        data={"name": "Malha", "category": "malha", "estacoes": ["verao", "verao"]},
    )
    assert editada.status_code == 200
    assert editada.json()["estacoes"] == ["verao"]


# ── Achado M17: o tri-estado de serve_chuva tem que sobreviver à edição ─────
# O frontend mandava sempre 'true'/'false'. Uma peça salva como "não informado"
# — que é o que a análise por regras produz de propósito, porque serve-chuva
# depende do material — virava `false` na primeira edição, mesmo sem ninguém
# tocar no campo. O valor ainda ia para o prompt pago.
#
# A correção é no formulário (terceira opção + omitir o campo quando nulo).
# Estes testes fixam o contrato do backend do qual ela depende.
def test_omitir_serve_chuva_na_criacao_grava_nulo(client, conta):
    r = client.post(
        "/api/wardrobe/items",
        headers=_auth(conta),
        data={"name": "Blazer", "category": "blazer"},
        files={"image": ("p.png", _png(), "image/png")},
    )
    assert r.status_code == 201
    assert r.json()["serve_chuva"] is None


def test_omitir_serve_chuva_na_edicao_preserva_o_nulo(client, conta):
    criada = client.post(
        "/api/wardrobe/items",
        headers=_auth(conta),
        data={"name": "Casaco", "category": "casaco"},
        files={"image": ("p.png", _png(), "image/png")},
    )
    item_id = criada.json()["id"]
    assert criada.json()["serve_chuva"] is None

    editada = client.put(
        f"/api/wardrobe/items/{item_id}",
        headers=_auth(conta),
        data={"name": "Casaco", "category": "casaco"},
    )
    assert editada.status_code == 200
    assert editada.json()["serve_chuva"] is None, (
        "omitir o campo tem que preservar o 'não informado' — se virar false, "
        "a edição afirma algo que a pessoa não disse"
    )


def test_enviar_serve_chuva_continua_funcionando(client, conta):
    criada = client.post(
        "/api/wardrobe/items",
        headers=_auth(conta),
        data={"name": "Capa", "category": "casaco", "serve_chuva": "true"},
        files={"image": ("p.png", _png(), "image/png")},
    )
    assert criada.json()["serve_chuva"] is True

    editada = client.put(
        f"/api/wardrobe/items/{criada.json()['id']}",
        headers=_auth(conta),
        data={"name": "Capa", "category": "casaco", "serve_chuva": "false"},
    )
    assert editada.json()["serve_chuva"] is False
