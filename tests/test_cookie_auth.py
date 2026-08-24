"""
Autenticação por cookie httpOnly.

O que muda: o token deixa de viver no localStorage do navegador (alcançável por
qualquer XSS) e passa a viajar num cookie que o JavaScript não lê.

O que isso CUSTA, e que estes testes cobrem: o navegador passa a enviar a
credencial sozinho, o que abre CSRF. As defesas são SameSite=Lax e CORS
restrito — e há teste para as duas.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.rate_limit import limiter
from app.main import app
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
    """O que está sob teste aqui é o cookie, não o teto de tentativas."""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def conta(client, monkeypatch):
    monkeypatch.setattr(auth_service, "send_email", lambda m: True)
    email = f"cookie-{uuid.uuid4().hex}@exemplo.com"
    client.post(
        "/api/auth/register", json={"name": "Cookie", "email": email, "password": SENHA}
    )
    yield email
    client.cookies.clear()
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).one_or_none()
        if u:
            db.delete(u)
            db.commit()
    finally:
        db.close()


def _login(client, email):
    return client.post("/api/auth/login", json={"email": email, "password": SENHA})


# ── O cookie ────────────────────────────────────────────────────────────────
def test_o_login_seta_um_cookie_httponly(client, conta):
    resp = _login(client, conta)
    assert resp.status_code == 200
    cookie = resp.cookies.get(settings.AUTH_COOKIE_NAME)
    assert cookie, "o login precisa setar o cookie de sessão"

    bruto = resp.headers["set-cookie"].lower()
    assert "httponly" in bruto, "sem HttpOnly, o XSS continua alcançando o token"
    assert "samesite=lax" in bruto, "sem SameSite, o cookie abre CSRF"
    assert "path=/" in bruto


def test_o_cookie_sozinho_autentica(client, conta):
    _login(client, conta)  # o TestClient guarda o cookie
    resp = client.get("/api/auth/me")  # sem header Authorization
    assert resp.status_code == 200
    assert resp.json()["email"] == conta


def test_o_header_authorization_continua_valendo(client, conta):
    """
    Clientes não-navegador (scripts, o app mobile de amanhã) não têm por que
    lidar com cookie. O header continua aceito.
    """
    token = _login(client, conta).json()["access_token"]
    limpo = TestClient(app)
    resp = limpo.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_o_logout_apaga_o_cookie(client, conta):
    _login(client, conta)
    saida = client.post("/api/auth/logout")
    assert saida.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_sem_cookie_e_sem_header_e_401(client):
    limpo = TestClient(app)
    assert limpo.get("/api/auth/me").status_code == 401


def test_cookie_lixo_e_401_e_nao_500(client):
    limpo = TestClient(app)
    limpo.cookies.set(settings.AUTH_COOKIE_NAME, "isto-nao-e-um-jwt")
    assert limpo.get("/api/auth/me").status_code == 401


def test_o_header_manda_quando_os_dois_vem_juntos(client, conta):
    """
    Regra explícita para não ficar indefinida: o header é o canal explícito de
    quem sabe o que está fazendo, então ele manda.
    """
    _login(client, conta)
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer lixo"})
    assert resp.status_code == 401, (
        "o header inválido não pode ser ignorado em favor do cookie"
    )


# ── Defesa contra CSRF ──────────────────────────────────────────────────────
def test_o_cors_nao_libera_origem_desconhecida_com_credenciais(client, conta):
    """
    Com cookie automático, CORS deixa de ser cosmético: é o que impede outro
    site de ler a resposta de uma requisição autenticada.
    """
    _login(client, conta)
    resp = client.get(
        "/api/auth/me", headers={"Origin": "https://site-do-atacante.example"}
    )
    assert (
        resp.headers.get("access-control-allow-origin")
        != "https://site-do-atacante.example"
    )


def test_a_flag_secure_segue_a_configuracao(client, conta, monkeypatch):
    """
    Localmente o cookie não pode ser `Secure` — a API roda em http e o navegador
    descartaria. Em produção precisa ser, e é o que a flag controla.
    """
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    limpo = TestClient(app)
    resp = limpo.post("/api/auth/login", json={"email": conta, "password": SENHA})
    assert "secure" in resp.headers["set-cookie"].lower()
