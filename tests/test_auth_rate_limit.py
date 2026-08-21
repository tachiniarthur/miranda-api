"""
Testes do rate limiting das rotas de autenticação.

Exercitam o comportamento pela borda HTTP (TestClient), que é onde o limite de
fato age. O banco é substituído por um stub: o que está sob teste é a contagem
de tentativas, não a autenticação em si — e um stub mantém o teste rápido e
independente de haver Postgres no ambiente.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter
from app.main import app


class _NoUserSession:
    """Sessão falsa: nenhum usuário existe, nada é persistido."""

    def scalar(self, *_args, **_kwargs):
        return None

    def execute(self, *_args, **_kwargs):
        return None

    def add(self, *_args, **_kwargs):
        return None

    def commit(self):
        return None

    def get(self, *_args, **_kwargs):
        return None

    def refresh(self, *_args, **_kwargs):
        return None


def _fake_db():
    yield _NoUserSession()


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = _fake_db
    # Zera as janelas entre testes: sem isso, o primeiro teste a estourar o
    # limite contaminaria os seguintes (a janela é de 15 minutos).
    limiter.reset()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        limiter.reset()


LOGIN = "/api/auth/login"
CREDS = {"email": "alvo@exemplo.com", "password": "senha-qualquer-123"}


def test_limite_configurado_em_cinco_por_quinze_minutos():
    assert AUTH_RATE_LIMIT == "5/15 minutes"


def test_cinco_tentativas_passam_a_sexta_e_bloqueada(client):
    """As 5 primeiras chegam ao endpoint (401); a 6ª é barrada antes dele."""
    for i in range(5):
        r = client.post(LOGIN, json=CREDS)
        assert r.status_code == 401, f"tentativa {i + 1} deveria chegar ao login"

    r = client.post(LOGIN, json=CREDS)
    assert r.status_code == 429
    assert "Muitas tentativas" in r.json()["detail"]


def test_resposta_429_traz_retry_after_e_cabecalhos(client):
    for _ in range(5):
        client.post(LOGIN, json=CREDS)
    r = client.post(LOGIN, json=CREDS)

    assert r.status_code == 429
    # Retry-After em segundos: quanto falta para a janela liberar uma vaga.
    retry_after = int(r.headers["Retry-After"])
    assert 0 < retry_after <= 15 * 60
    assert r.headers["X-RateLimit-Limit"] == "5"
    assert r.headers["X-RateLimit-Remaining"] == "0"


def test_mensagem_de_429_nao_revela_se_o_email_existe(client):
    """O 429 não pode virar o oráculo de enumeração que o 401 genérico fecha."""
    for _ in range(6):
        client.post(LOGIN, json=CREDS)
    bloqueado = client.post(LOGIN, json=CREDS)

    assert bloqueado.status_code == 429
    corpo = bloqueado.text.lower()
    assert "alvo@exemplo.com" not in corpo
    assert "não encontrado" not in corpo
    assert "cadastrad" not in corpo


def test_email_diferente_tem_cota_propria(client):
    """A cota é por (IP, e-mail): esgotar uma conta não bloqueia outra."""
    for _ in range(6):
        client.post(LOGIN, json=CREDS)
    assert client.post(LOGIN, json=CREDS).status_code == 429

    outro = client.post(
        LOGIN, json={"email": "outro@exemplo.com", "password": "senha-qualquer-123"}
    )
    assert outro.status_code == 401


def test_maiusculas_no_email_nao_dobram_a_cota(client):
    """"ALVO@Exemplo.com" e "alvo@exemplo.com" compartilham a mesma cota."""
    for _ in range(5):
        client.post(LOGIN, json=CREDS)

    r = client.post(
        LOGIN, json={"email": "ALVO@Exemplo.com", "password": "senha-qualquer-123"}
    )
    assert r.status_code == 429


@pytest.mark.parametrize(
    "rota,corpo",
    [
        ("/api/auth/login", CREDS),
        (
            "/api/auth/register",
            {
                "name": "Fulano",
                "email": "alvo@exemplo.com",
                "password": "senha-qualquer-123",
            },
        ),
        ("/api/auth/forgot-password", {"email": "alvo@exemplo.com"}),
        (
            "/api/auth/reset-password",
            {"token": "token-invalido", "new_password": "outra-senha-123"},
        ),
    ],
)
def test_as_quatro_rotas_de_auth_sao_limitadas(client, rota, corpo):
    codigos = [client.post(rota, json=corpo).status_code for _ in range(6)]
    assert codigos[-1] == 429, f"{rota} não foi limitada: {codigos}"


def test_rotas_nao_de_auth_seguem_sem_limite(client):
    """O limite é declarado por rota; nada global foi imposto ao resto da API."""
    for _ in range(20):
        assert client.get("/api/health").status_code == 200
