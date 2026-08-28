"""
Trabalho pesado de CPU não pode rodar no event loop (achado M5).

`analyze_item` é `async def` e chamava `analyze_clothing_item_detailed` — que é
`def` comum e roda o FashionCLIP, descrito pelo próprio projeto como o gasto de
CPU mais caro — sem `await`. Numa função assíncrona isso executa NO event loop,
bloqueando o worker inteiro pela duração da inferência: nenhuma outra
requisição é atendida enquanto isso, nem `/api/health`, nem `/api/auth/login`.

O mesmo padrão, em escala menor, estava em `perceptual_hash` dentro do
`create_item` assíncrono.

Como se verifica: `run_in_threadpool` do Starlette delega ao `anyio`, que
executa em threads nomeadas "AnyIO worker thread". Se a chamada acontecer no
event loop, a thread é outra — e o teste falha.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import io
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.user import User


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (30, 70, 110)).save(buf, format="PNG")
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
def conta():
    db = SessionLocal()
    u = User(
        name="CPU",
        email=f"cpu-{uuid.uuid4().hex[:8]}@exemplo.com",
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
    token = create_access_token(str(u.id), token_version=u.token_version)
    return {"Authorization": f"Bearer {token}"}


def test_a_analise_roda_fora_do_event_loop(client, conta, monkeypatch):
    from app.api.routes import wardrobe

    threads: list[str] = []

    def _fake_analyze(contents: bytes):
        threads.append(threading.current_thread().name)
        raise wardrobe.NotClothingError("não é roupa")

    monkeypatch.setattr(wardrobe, "analyze_clothing_item_detailed", _fake_analyze)

    client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(conta),
        files={"image": ("p.png", _png(), "image/png")},
    )

    assert threads, "a análise não chegou a ser chamada"
    assert "AnyIO worker" in threads[0], (
        f"a inferência rodou em {threads[0]!r} — se for a thread do event loop, "
        "uma análise trava todas as outras requisições do worker"
    )


def test_o_hash_perceptual_roda_fora_do_event_loop(client, conta, monkeypatch):
    from app.services import wardrobe_service

    threads: list[str] = []
    real = wardrobe_service.perceptual_hash

    def _spy(contents: bytes):
        threads.append(threading.current_thread().name)
        return real(contents)

    monkeypatch.setattr(wardrobe_service, "perceptual_hash", _spy)

    r = client.post(
        "/api/wardrobe/items",
        headers=_auth(conta),
        data={"name": "Peça", "category": "camisa"},
        files={"image": ("p.png", _png(), "image/png")},
    )
    assert r.status_code == 201

    assert threads, "o hash perceptual não chegou a ser chamado"
    assert "AnyIO worker" in threads[0], (
        f"o hash rodou em {threads[0]!r} — Pillow abrindo e redimensionando a "
        "imagem no event loop bloqueia o worker"
    )
