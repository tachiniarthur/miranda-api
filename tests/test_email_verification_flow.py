"""
Fluxo de verificação de e-mail, pela borda HTTP.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.

O que estes testes protegem, em ordem de importância:
  1. o token não vaza pela resposta HTTP (só pelo e-mail);
  2. o reenvio responde igual exista ou não a conta (senão vira enumeração);
  3. token usado, expirado ou inventado é recusado do mesmo jeito;
  4. a flag de bloqueio de login funciona nos dois estados.

O envio é interceptado em `app.services.auth_service.send_email` — o nome que o
SERVIÇO enxerga —, porque ele importa a função por valor: trocar o atributo no
módulo de origem não o alcançaria.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.rate_limit import limiter
from app.main import app
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User
from app.services import auth_service

SENHA = "Uma-Senha-Muito-Longa-9"


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
def _sem_rate_limit_residual():
    """
    Zera a janela do rate limit de auth entre os testes.

    `verify-email` não carrega e-mail no corpo, então sua cota é por IP: sem
    este reset, o sexto pedido do MÓDULO levaria 429 e o teste falharia por um
    motivo que não é o que ele investiga. O rate limit em si tem suíte própria
    (tests/test_auth_rate_limit.py).
    """
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def caixa(monkeypatch):
    """Substitui o envio real e devolve a lista do que foi 'enviado'."""
    enviados = []
    monkeypatch.setattr(
        auth_service, "send_email", lambda m: enviados.append(m) or True
    )
    return enviados


def _limpa(db, email):
    u = db.query(User).filter(User.email == email).one_or_none()
    if u:
        db.query(EmailVerificationToken).filter(
            EmailVerificationToken.user_id == u.id
        ).delete()
        db.delete(u)
        db.commit()


def _token_do_email(texto: str) -> str:
    import re

    achado = re.search(r"token=([A-Za-z0-9_\-]{20,})", texto)
    assert achado, f"nenhum token no corpo do e-mail: {texto!r}"
    return achado.group(1)


@pytest.fixture
def cadastrado(client, db, caixa):
    email = f"verif-{uuid.uuid4().hex}@exemplo.com"
    resp = client.post(
        "/api/auth/register",
        json={"name": "Quem Verifica", "email": email, "password": SENHA},
    )
    assert resp.status_code in (200, 201), resp.text
    yield email
    _limpa(db, email)


# ── Envio no cadastro ───────────────────────────────────────────────────────
def test_o_cadastro_dispara_o_email_de_verificacao(cadastrado, caixa):
    assert len(caixa) == 1
    assert caixa[0].to == cadastrado
    _token_do_email(caixa[0].text)


def test_o_token_nunca_aparece_na_resposta_http(client, db, caixa):
    email = f"verif-{uuid.uuid4().hex}@exemplo.com"
    resp = client.post(
        "/api/auth/register",
        json={"name": "X", "email": email, "password": SENHA},
    )
    try:
        token = _token_do_email(caixa[0].text)
        assert token not in resp.text
    finally:
        _limpa(db, email)


def test_conta_nova_se_declara_nao_verificada(client, cadastrado, caixa):
    login = client.post(
        "/api/auth/login", json={"email": cadastrado, "password": SENHA}
    )
    assert login.status_code == 200
    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.json()["email_verified"] is False


# ── Confirmação ─────────────────────────────────────────────────────────────
def test_token_valido_verifica_a_conta(client, db, cadastrado, caixa):
    token = _token_do_email(caixa[0].text)
    resp = client.post("/api/auth/verify-email", json={"token": token})
    assert resp.status_code == 200

    user = db.query(User).filter(User.email == cadastrado).one()
    db.refresh(user)
    assert user.is_email_verified is True


def test_o_token_nao_serve_duas_vezes(client, cadastrado, caixa):
    token = _token_do_email(caixa[0].text)
    primeira = client.post("/api/auth/verify-email", json={"token": token})
    assert primeira.status_code == 200
    segunda = client.post("/api/auth/verify-email", json={"token": token})
    assert segunda.status_code == 400


def test_token_inventado_e_recusado(client):
    resp = client.post(
        "/api/auth/verify-email", json={"token": "nao-existe-" + "x" * 30}
    )
    assert resp.status_code == 400


def test_token_expirado_e_recusado(client, db, cadastrado, caixa):
    token = _token_do_email(caixa[0].text)
    user = db.query(User).filter(User.email == cadastrado).one()
    row = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.user_id == user.id)
        .one()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    resp = client.post("/api/auth/verify-email", json={"token": token})
    assert resp.status_code == 400


def test_a_recusa_e_identica_para_invalido_e_expirado(client, db, cadastrado, caixa):
    """
    Distinguir "inválido" de "expirado" diria a um atacante que ele acertou um
    token que existiu — informação que não tem por que sair daqui.
    """
    inventado = client.post("/api/auth/verify-email", json={"token": "z" * 40})

    token = _token_do_email(caixa[0].text)
    user = db.query(User).filter(User.email == cadastrado).one()
    row = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.user_id == user.id)
        .one()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    expirado = client.post("/api/auth/verify-email", json={"token": token})

    assert inventado.status_code == expirado.status_code
    assert inventado.json() == expirado.json()


# ── Reenvio ─────────────────────────────────────────────────────────────────
def test_o_reenvio_manda_token_novo_para_conta_pendente(client, cadastrado, caixa):
    caixa.clear()
    resp = client.post("/api/auth/resend-verification", json={"email": cadastrado})
    assert resp.status_code == 200
    assert len(caixa) == 1


def test_o_reenvio_responde_igual_para_endereco_desconhecido(
    client, cadastrado, caixa
):
    conhecido = client.post(
        "/api/auth/resend-verification", json={"email": cadastrado}
    )
    caixa.clear()
    desconhecido = client.post(
        "/api/auth/resend-verification", json={"email": "ninguem@exemplo.com"}
    )
    assert conhecido.status_code == desconhecido.status_code == 200
    assert conhecido.json() == desconhecido.json()
    assert caixa == [], "não há conta: não há para quem enviar"


def test_o_reenvio_revoga_o_token_anterior(client, cadastrado, caixa):
    primeiro = _token_do_email(caixa[0].text)
    caixa.clear()
    client.post("/api/auth/resend-verification", json={"email": cadastrado})
    segundo = _token_do_email(caixa[0].text)
    assert primeiro != segundo

    # O antigo morreu; só o novo vale.
    velho = client.post("/api/auth/verify-email", json={"token": primeiro})
    assert velho.status_code == 400
    novo = client.post("/api/auth/verify-email", json={"token": segundo})
    assert novo.status_code == 200


# ── A flag de bloqueio ──────────────────────────────────────────────────────
def test_login_funciona_sem_verificacao_por_padrao(client, cadastrado, caixa):
    assert settings.REQUIRE_VERIFIED_EMAIL is False
    resp = client.post(
        "/api/auth/login", json={"email": cadastrado, "password": SENHA}
    )
    assert resp.status_code == 200


def test_login_e_bloqueado_com_a_flag_ligada(client, cadastrado, caixa, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_VERIFIED_EMAIL", True)
    resp = client.post(
        "/api/auth/login", json={"email": cadastrado, "password": SENHA}
    )
    assert resp.status_code == 403


def test_o_bloqueio_nao_revela_quais_contas_existem(
    client, cadastrado, caixa, monkeypatch
):
    """
    Bloquear por não-verificado só pode acontecer DEPOIS de a senha conferir.
    Se a rota respondesse 403 antes disso, bastaria tentar um e-mail qualquer
    para descobrir se ele tem conta.
    """
    monkeypatch.setattr(settings, "REQUIRE_VERIFIED_EMAIL", True)
    senha_errada = client.post(
        "/api/auth/login", json={"email": cadastrado, "password": "senha-errada-aqui-1"}
    )
    inexistente = client.post(
        "/api/auth/login",
        json={"email": "ninguem@exemplo.com", "password": "senha-errada-aqui-1"},
    )
    assert senha_errada.status_code == inexistente.status_code == 401
    assert senha_errada.json() == inexistente.json()
