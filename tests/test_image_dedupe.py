"""
Recusa de reenvio da mesma imagem pelo mesmo usuário.

A quota (MAX_ITEMS_PER_USER) limita quantas peças cabem; isto limita quão barato
é enchê-la. Sem esta checagem, um script sobe a mesma foto 150 vezes e ocupa o
guarda-roupa inteiro com um único arquivo.

Por que perceptual e não SHA-256: um hash criptográfico muda inteiro se um byte
mudar, então recomprimir ou redimensionar já burla. O dHash sobrevive a essas
transformações.

Os testes de rota rodam contra o Postgres de DATABASE_URL; sem banco, são
pulados. Os do hash em si não precisam de banco.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.clothing_item import ClothingItem
from app.models.user import User
from app.services.image_validation import perceptual_hash


def _bytes(img: Image.Image, formato="PNG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=formato, **kw)
    return buf.getvalue()


def _foto(seed: int = 0) -> Image.Image:
    """
    Uma imagem com estrutura EM ESCALA GRANDE.

    O dHash enxerga uma miniatura de 9x8: textura fina vira ruído nesse tamanho
    e o hash de uma imagem chapada é degenerado. Blocos grandes são o que uma
    foto de peça tem e o que o algoritmo de fato compara.
    """
    img = Image.new("RGB", (200, 260), (240, 235, 220))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 25 + seed * 30, 180, 120], fill=(30 + seed * 70, 60, 90))
    d.ellipse([35, 140, 165, 245], fill=(210, 100 + seed * 60, 45))
    d.line([0, 130 + seed * 10, 200, 130 + seed * 10], fill=(20, 20, 20), width=6)
    return img


# ── O hash em si ────────────────────────────────────────────────────────────
def test_os_mesmos_bytes_dao_o_mesmo_hash():
    b = _bytes(_foto())
    assert perceptual_hash(b) == perceptual_hash(b)


def test_o_hash_sobrevive_a_recompressao():
    """
    O caminho barato de burlar: salvar de novo com outra qualidade.

    A qualidade alta é a que importa aqui — é o que um "salvar de novo" comum
    produz. Abaixo de ~90 o JPEG mexe o bastante em regiões chapadas para virar
    um bit ou dois; esse limite está documentado em `perceptual_hash` e é
    aceito de propósito (ver o teste seguinte).
    """
    img = _foto()
    a = perceptual_hash(_bytes(img, "JPEG", quality=95))
    b = perceptual_hash(_bytes(img, "JPEG", quality=92))
    assert a == b


def test_recompressao_agressiva_pode_escapar_e_isso_e_deliberado():
    """
    Fixa o limite conhecido, para que ele seja uma decisão e não uma surpresa.

    Aceitar "quase igual" (distância de Hamming) fecharia esta brecha e abriria
    outra pior: peças legítimas parecidas — duas camisas brancas — seriam
    recusadas. Recusar cadastro de verdade é pior do que deixar passar reenvio.
    """
    img = _foto()
    original = perceptual_hash(_bytes(img, "JPEG", quality=95))
    esmagada = perceptual_hash(_bytes(img, "JPEG", quality=40))
    assert original != esmagada


def test_o_hash_sobrevive_ao_redimensionamento():
    img = _foto()
    a = perceptual_hash(_bytes(img))
    b = perceptual_hash(_bytes(img.resize((100, 130))))
    assert a == b


def test_o_hash_sobrevive_a_troca_de_formato():
    img = _foto()
    assert perceptual_hash(_bytes(img, "PNG")) == perceptual_hash(
        _bytes(img, "JPEG", quality=92)
    )


def test_imagem_sem_estrutura_nao_gera_hash():
    """
    Fundo chapado dá 64 bits iguais, e duas peças diferentes assim colidiriam —
    a checagem recusaria um cadastro legítimo. Sem hash, sem checagem.
    """
    chapada = Image.new("RGB", (200, 260), (250, 250, 250))
    assert perceptual_hash(_bytes(chapada)) is None


def test_imagens_diferentes_dao_hashes_diferentes():
    assert perceptual_hash(_bytes(_foto(0))) != perceptual_hash(_bytes(_foto(1)))


def test_o_hash_tem_forma_estavel():
    """16 caracteres hexadecimais: 64 bits de dHash. A coluna tem esse tamanho."""
    h = perceptual_hash(_bytes(_foto()))
    assert len(h) == 16
    int(h, 16)  # levanta se não for hexadecimal


def test_imagem_corrompida_nao_levanta():
    """
    Bytes inválidos já são recusados por `validate_image_bytes` antes daqui.
    Se ainda assim chegarem, o hash devolve None em vez de derrubar o upload.
    """
    assert perceptual_hash(b"isto nao e uma imagem") is None


# ── A recusa na rota ────────────────────────────────────────────────────────
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
def usuario(db):
    u = User(
        name="Quem Repete",
        email=f"dedupe-{uuid.uuid4().hex}@exemplo.com",
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


def test_reenviar_a_mesma_imagem_e_recusado(client, db, usuario):
    img = _bytes(_foto())
    primeira = client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("a.png", img, "image/png")},
    )
    assert primeira.status_code == 201

    segunda = client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "outro nome", "category": "calca"},
        files={"image": ("b.png", img, "image/png")},
    )
    assert segunda.status_code == 409
    assert "imagem" in segunda.text.lower()


def test_reenvio_recomprimido_tambem_e_recusado(client, db, usuario):
    """O caminho barato de burlar precisa fechar junto."""
    img = _foto()
    client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("a.png", _bytes(img, "JPEG", quality=95), "image/jpeg")},
    )
    segunda = client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("b.jpg", _bytes(img, "JPEG", quality=92), "image/jpeg")},
    )
    assert segunda.status_code == 409


def test_outro_usuario_pode_subir_a_mesma_imagem(client, db, usuario):
    """
    A checagem é POR USUÁRIO. Duas pessoas podem legitimamente ter a mesma foto
    de catálogo da mesma peça, e recusar isso seria um bug de produto.
    """
    img = _bytes(_foto())
    client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("a.png", img, "image/png")},
    )

    outro = User(
        name="Outro",
        email=f"dup-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(outro)
    db.commit()
    db.refresh(outro)
    try:
        resp = client.post(
            "/api/wardrobe/items",
            headers=_auth(outro),
            data={"name": "camisa", "category": "camisa"},
            files={"image": ("a.png", img, "image/png")},
        )
        assert resp.status_code == 201
    finally:
        db.query(ClothingItem).filter(ClothingItem.user_id == outro.id).delete()
        db.delete(outro)
        db.commit()


def test_imagem_diferente_continua_passando(client, db, usuario):
    for i, nome in enumerate(["a.png", "b.png"]):
        resp = client.post(
            "/api/wardrobe/items",
            headers=_auth(usuario),
            data={"name": f"peça {i}", "category": "camisa"},
            files={"image": (nome, _bytes(_foto(i)), "image/png")},
        )
        assert resp.status_code == 201
