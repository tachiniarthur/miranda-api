"""
Testes da rota autenticada de imagem e da validação de upload pela borda HTTP.

Cobre os itens #9 (magic bytes validados nas rotas de criação/atualização e em
/analyze) e #10 (imagem servida por rota autenticada, com conferência de posse,
no lugar do mount estático público) da revisão de segurança.

Rodam contra o Postgres configurado em DATABASE_URL — é a posse da peça que está
sob teste, e ela vive no banco. Sem banco acessível, os testes são PULADOS.
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
from app.services.image_validation import MAX_FILE_BYTES
from app.services.storage import image_storage


def _png_bytes(cor=(120, 60, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), cor).save(buf, format="PNG")
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
        name="Dona da Peça",
        email=f"img-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,  # nunca usado: token é criado direto
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


def _auth(user: User) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.fixture
def peca(client, dona, db):
    """Cria uma peça de verdade pela API e limpa o arquivo ao final."""
    r = client.post(
        "/api/wardrobe/items",
        headers=_auth(dona),
        data={"name": "Camisa de teste", "category": "camisa"},
        files={"image": ("camisa.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 201, r.text
    item = r.json()
    yield item

    registro = db.get(ClothingItem, uuid.UUID(item["id"]))
    if registro is not None:
        image_storage.delete(registro.image_path)


# ── #10: a imagem sai por rota autenticada ────────────────────────────

def test_image_url_aponta_para_a_rota_autenticada(peca):
    assert peca["image_url"].endswith(f"/api/wardrobe/items/{peca['id']}/image")
    assert "/static/" not in peca["image_url"]


def test_dona_consegue_baixar_a_propria_imagem(client, dona, peca):
    r = client.get(f"/api/wardrobe/items/{peca['id']}/image", headers=_auth(dona))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_sem_token_a_imagem_e_recusada(client, peca):
    r = client.get(f"/api/wardrobe/items/{peca['id']}/image")
    assert r.status_code == 401


def test_token_invalido_e_recusado(client, peca):
    r = client.get(
        f"/api/wardrobe/items/{peca['id']}/image",
        headers={"Authorization": "Bearer nao-e-um-token"},
    )
    assert r.status_code == 401


def test_outro_usuario_nao_acessa_a_imagem(client, intrusa, peca):
    """O cerne do item #10: posse conferida antes de devolver o arquivo."""
    r = client.get(f"/api/wardrobe/items/{peca['id']}/image", headers=_auth(intrusa))
    assert r.status_code == 404
    assert r.content[:8] != b"\x89PNG\r\n\x1a\n"


def test_peca_inexistente_responde_404(client, dona):
    r = client.get(
        f"/api/wardrobe/items/{uuid.uuid4()}/image", headers=_auth(dona)
    )
    assert r.status_code == 404


def test_imagem_nao_vai_para_cache_compartilhado(client, dona, peca):
    """Conteúdo por usuário não pode ficar em cache de proxy/CDN."""
    r = client.get(f"/api/wardrobe/items/{peca['id']}/image", headers=_auth(dona))
    assert "private" in r.headers["cache-control"]


def test_mount_estatico_publico_nao_existe_mais(client, db, peca):
    """A rota antiga servia qualquer imagem a quem tivesse a URL."""
    registro = db.get(ClothingItem, uuid.UUID(peca["id"]))
    r = client.get(f"/static/clothing_images/{registro.image_path}")
    assert r.status_code == 404


def test_imagem_tambem_traz_os_headers_de_seguranca(client, dona, peca):
    """nosniff importa especialmente em arquivo enviado por usuário."""
    r = client.get(f"/api/wardrobe/items/{peca['id']}/image", headers=_auth(dona))
    assert r.headers["X-Content-Type-Options"] == "nosniff"


# ── #9: magic bytes nas rotas de upload ───────────────────────────────

def test_criacao_recusa_arquivo_que_nao_e_imagem(client, dona):
    """Content-Type diz PNG, conteúdo é HTML: vale o conteúdo."""
    r = client.post(
        "/api/wardrobe/items",
        headers=_auth(dona),
        data={"name": "Falsa", "category": "camisa"},
        files={
            "image": ("x.png", b"<html><script>alert(1)</script></html>", "image/png")
        },
    )
    assert r.status_code == 400
    assert "imagem" in r.json()["detail"].lower()


def test_criacao_recusa_content_type_fora_da_allowlist(client, dona):
    r = client.post(
        "/api/wardrobe/items",
        headers=_auth(dona),
        data={"name": "Falsa", "category": "camisa"},
        files={"image": ("x.pdf", _png_bytes(), "application/pdf")},
    )
    assert r.status_code == 400


def test_atualizacao_recusa_arquivo_que_nao_e_imagem(client, dona, peca):
    r = client.put(
        f"/api/wardrobe/items/{peca['id']}",
        headers=_auth(dona),
        data={"name": "Camisa de teste", "category": "camisa"},
        files={"image": ("x.png", b"nao sou uma imagem", "image/png")},
    )
    assert r.status_code == 400


def test_atualizacao_mantem_a_imagem_antiga_quando_a_nova_e_recusada(
    client, dona, peca, db
):
    """Recusa não pode deixar a peça sem imagem."""
    antes = db.get(ClothingItem, uuid.UUID(peca["id"])).image_path
    client.put(
        f"/api/wardrobe/items/{peca['id']}",
        headers=_auth(dona),
        data={"name": "Camisa de teste", "category": "camisa"},
        files={"image": ("x.png", b"lixo", "image/png")},
    )
    db.expire_all()
    assert db.get(ClothingItem, uuid.UUID(peca["id"])).image_path == antes
    assert image_storage.path_for(antes).is_file()


def test_analyze_recusa_arquivo_que_nao_e_imagem(client, dona):
    """
    A recusa acontece ANTES do FashionCLIP.

    Se a validação não estivesse na frente, este teste carregaria ~600 MB de
    modelo para então falhar — o próprio tempo de execução é a evidência.
    """
    r = client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(dona),
        files={"image": ("x.png", b"<html>nao sou imagem</html>", "image/png")},
    )
    assert r.status_code == 400


def test_analyze_recusa_content_type_fora_da_allowlist(client, dona):
    r = client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(dona),
        files={"image": ("x.txt", b"texto puro", "text/plain")},
    )
    assert r.status_code == 415


def test_analyze_recusa_arquivo_grande_demais(client, dona):
    grande = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_FILE_BYTES + 1)
    r = client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(dona),
        files={"image": ("grande.png", grande, "image/png")},
    )
    assert r.status_code == 413


def test_analyze_recusa_arquivo_vazio(client, dona):
    r = client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(dona),
        files={"image": ("vazio.png", b"", "image/png")},
    )
    assert r.status_code == 400


def test_analyze_exige_autenticacao(client):
    r = client.post(
        "/api/wardrobe/items/analyze",
        files={"image": ("x.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 401
