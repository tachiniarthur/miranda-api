"""
O histórico recente vira contexto da próxima geração.

Roda contra o Postgres de DATABASE_URL — é `looks_history` que está sob teste, e
ela vive no banco. Sem banco acessível, os testes são PULADOS (mesmo padrão de
tests/test_wardrobe_image_access.py).
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.core.database import SessionLocal, engine
from app.models.clothing_item import ClothingItem
from app.models.enums import ClothingCategory, CondicaoClimatica, Formalidade, Ocasiao, PesoTermico
from app.models.look_history import LookHistory
from app.models.user import User
from app.schemas.look import GenerateLookRequest
from app.services import look_service
from app.services.ai import look_generation
from app.services.ai.claude_client import ClaudeReply, ClaudeUsage
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


def test_generate_look_persists_a_history_row_and_resolves_images(db, monkeypatch):
    """
    Fecha a lacuna que `scripts/validate_look_live.py` deixa aberta de propósito:
    a validação ao vivo (Task 8) chama `generate_daily_look` direto e nunca passa
    pela camada de serviço, então a gravação em `looks_history` — e a resolução
    de `image_url` — nunca foram exercitadas por ela. Este teste cobre exatamente
    esse trecho, com a chamada à API substituída por um dublê (custo zero).
    """
    owner = User(
        name="Dona da Persistência",
        email=f"persist-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)

    bottom = ClothingItem(
        user_id=owner.id,
        name="Calça preta de teste",
        category=ClothingCategory.CALCA,
        image_path="clothing_images/teste-calca.png",
        cor_primaria="preto",
        formalidade=Formalidade.CASUAL,
        peso_termico=PesoTermico.MEDIO,
        serve_chuva=False,
    )
    top = ClothingItem(
        user_id=owner.id,
        name="Camisa branca de teste",
        category=ClothingCategory.CAMISA,
        image_path="clothing_images/teste-camisa.png",
        cor_primaria="branco",
        formalidade=Formalidade.CASUAL,
        peso_termico=PesoTermico.LEVE,
        serve_chuva=False,
    )
    db.add_all([bottom, top])
    db.commit()

    bottom_id, top_id = str(bottom.id), str(top.id)
    commentary = "Calça preta e camisa branca compõem o básico do dia ameno."
    reply_text = json.dumps({
        "looks": [{
            "label": "I",
            "items": [
                {"item_id": bottom_id, "role": "peça de baixo"},
                {"item_id": top_id, "role": "peça de cima"},
            ],
            "commentary": commentary,
        }],
        "note": None,
    }, ensure_ascii=False)

    def fake_request_composition(system, user_message, schema):
        return ClaudeReply(
            text=reply_text,
            usage=ClaudeUsage(input_tokens=10, output_tokens=20, model="claude-opus-5"),
        )

    monkeypatch.setattr(
        look_generation.claude_client, "request_composition", fake_request_composition
    )

    try:
        payload = GenerateLookRequest(
            temperatura_min=16.0,
            temperatura_max=24.0,
            condicoes_climaticas=[CondicaoClimatica.SOL],
            ocasiao=Ocasiao.DIA_A_DIA,
        )
        response = look_service.generate_look(db, user_id=owner.id, payload=payload)

        # ── Resposta devolvida ao chamador ───────────────────────────────
        assert len(response.looks) == 1
        look = response.looks[0]
        assert {i.item_id for i in look.items} == {bottom.id, top.id}
        for piece in look.items:
            assert piece.image_url  # resolvida por `authenticated_image_url`
            assert str(piece.item_id) in piece.image_url

        # ── Linha gravada em looks_history ───────────────────────────────
        rows = (
            db.query(LookHistory)
            .filter(LookHistory.user_id == owner.id)
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.temperatura_min == 16.0
        assert row.temperatura_max == 24.0
        assert row.condicao_climatica == "sol"
        assert row.ocasiao == "dia_a_dia"
        assert row.itens_sugeridos["note"] is None
        persisted_looks = row.itens_sugeridos["looks"]
        assert len(persisted_looks) == 1
        assert set(persisted_looks[0]["item_ids"]) == {bottom_id, top_id}
        assert persisted_looks[0]["commentary"] == commentary
        assert commentary in row.justificativa
    finally:
        db.query(LookHistory).filter(LookHistory.user_id == owner.id).delete()
        db.query(ClothingItem).filter(ClothingItem.user_id == owner.id).delete()
        db.delete(owner)
        db.commit()
