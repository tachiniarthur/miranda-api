"""
Enumeração de contas no cadastro (item #4 da revisão de segurança).

O login já não vaza — nem por mensagem, nem por tempo. O cadastro vazava, e
por dois canais: a mensagem 409 e o custo do bcrypt, que só era pago no caminho
do e-mail novo.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.

O envio é interceptado em `app.services.auth_service.send_email` — o nome que o
serviço enxerga —, porque ele importa a função por valor.
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from fastapi.testclient import TestClient

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
    Zera a janela do rate limit entre os testes: vários deles cadastram o MESMO
    e-mail mais de uma vez de propósito, e o teto de 5/15min barraria o pedido
    por um motivo que não é o que está sob teste (esse tem suíte própria em
    tests/test_auth_rate_limit.py).
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


@pytest.fixture
def existente(client, db, caixa):
    email = f"dono-{uuid.uuid4().hex}@exemplo.com"
    client.post(
        "/api/auth/register", json={"name": "Dono", "email": email, "password": SENHA}
    )
    caixa.clear()
    yield email
    _limpa(db, email)


def _cadastrar(client, email):
    return client.post(
        "/api/auth/register", json={"name": "Alguém", "email": email, "password": SENHA}
    )


# ── O canal da mensagem ─────────────────────────────────────────────────────
def test_cadastrar_com_email_existente_e_indistinguivel(client, db, existente, caixa):
    novo = f"novo-{uuid.uuid4().hex}@exemplo.com"
    try:
        repetido = _cadastrar(client, existente)
        inedito = _cadastrar(client, novo)
        assert repetido.status_code == inedito.status_code
        assert repetido.json() == inedito.json()
    finally:
        _limpa(db, novo)


def test_nenhuma_segunda_conta_e_criada(client, db, existente, caixa):
    _cadastrar(client, existente)
    assert db.query(User).filter(User.email == existente).count() == 1


def test_o_dono_e_avisado_por_email(client, existente, caixa):
    _cadastrar(client, existente)
    assert len(caixa) == 1
    assert caixa[0].to == existente
    assert "tentou criar uma conta" in caixa[0].text.lower()


def test_o_aviso_nao_carrega_nada_acionavel(client, existente, caixa):
    """Quem disparou a tentativa pode não ser o dono: nada de token ou link."""
    _cadastrar(client, existente)
    texto = caixa[0].text
    assert "token=" not in texto
    assert SENHA not in texto


def test_cadastro_realmente_novo_recebe_seu_email_de_verificacao(client, db, caixa):
    novo = f"novo-{uuid.uuid4().hex}@exemplo.com"
    try:
        _cadastrar(client, novo)
        assert len(caixa) == 1
        assert "confirm" in caixa[0].subject.lower()
    finally:
        _limpa(db, novo)


# ── O canal do relógio ──────────────────────────────────────────────────────
def test_os_dois_caminhos_custam_tempo_parecido(client, db, existente, caixa):
    """
    O caminho do e-mail novo paga bcrypt (~250 ms). Se o caminho do e-mail
    repetido não pagasse, a diferença seria mensurável numa única requisição e o
    relógio diria quais endereços têm conta — a mesma falha que o login já
    corrigiu rodando o bcrypt sempre.
    """
    criados = []

    def _mede_novo():
        email = f"t-{uuid.uuid4().hex}@exemplo.com"
        criados.append(email)
        limiter.reset()  # fora do relógio: mede-se o cadastro, não o limitador
        inicio = time.perf_counter()
        _cadastrar(client, email)
        return time.perf_counter() - inicio

    def _mede_repetido():
        limiter.reset()
        inicio = time.perf_counter()
        _cadastrar(client, existente)
        return time.perf_counter() - inicio

    try:
        novos = sorted(_mede_novo() for _ in range(5))
        repetidos = sorted(_mede_repetido() for _ in range(5))
    finally:
        for e in criados:
            _limpa(db, e)

    mediana_novo = statistics.median(novos)
    mediana_repetido = statistics.median(repetidos)
    razao = max(mediana_novo, mediana_repetido) / max(
        min(mediana_novo, mediana_repetido), 1e-6
    )
    assert razao < 2.0, (
        f"os dois caminhos precisam custar tempo comparável; "
        f"novo={mediana_novo:.3f}s repetido={mediana_repetido:.3f}s razão={razao:.2f}"
    )
