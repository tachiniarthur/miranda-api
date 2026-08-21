"""
Testes da configuração de segurança da aplicação.

Cobre os itens #11 (headers de segurança), #12 (guard de CORS wildcard com
credenciais) e #13 (HOST padrão em localhost) da revisão de segurança.

`Settings` é instanciada diretamente com os campos necessários, em vez de mexer
no ambiente do processo: os testes ficam independentes do `.env` da máquina.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.security_headers import SecurityHeadersMiddleware
from app.main import app

BASE = {"DATABASE_URL": "postgresql://x", "JWT_SECRET_KEY": "k" * 40}


# ── #13: HOST padrão ──────────────────────────────────────────────────

def test_host_padrao_e_somente_localhost():
    """0.0.0.0 publica a API em toda a rede local; não pode ser o padrão."""
    assert Settings(**BASE, _env_file=None).HOST == "127.0.0.1"


def test_host_pode_ser_aberto_deliberadamente():
    """Continua possível expor na rede — só precisa ser uma escolha explícita."""
    assert Settings(**BASE, _env_file=None, HOST="0.0.0.0").HOST == "0.0.0.0"


# ── #12: CORS wildcard com credenciais ────────────────────────────────

def test_cors_wildcard_com_credenciais_e_recusado():
    with pytest.raises(ValidationError) as exc:
        Settings(**BASE, _env_file=None, CORS_ORIGINS="*")
    assert "CORS_ORIGINS=*" in str(exc.value)


def test_cors_wildcard_no_meio_da_lista_tambem_e_recusado():
    """O guard olha a lista já separada, não a string crua."""
    with pytest.raises(ValidationError):
        Settings(**BASE, _env_file=None, CORS_ORIGINS="http://localhost:3000,*")


def test_cors_wildcard_sem_credenciais_e_permitido():
    """Sem credenciais a combinação deixa de ser perigosa — e é liberada."""
    s = Settings(
        **BASE, _env_file=None, CORS_ORIGINS="*", CORS_ALLOW_CREDENTIALS=False
    )
    assert s.cors_origins_list == ["*"]


def test_origens_explicitas_com_credenciais_seguem_validas():
    s = Settings(
        **BASE,
        _env_file=None,
        CORS_ORIGINS="http://localhost:3000,http://127.0.0.1:3000",
    )
    assert s.CORS_ALLOW_CREDENTIALS is True
    assert s.cors_origins_list == ["http://localhost:3000", "http://127.0.0.1:3000"]


# ── #11: headers de segurança ─────────────────────────────────────────

@pytest.fixture
def client():
    return TestClient(app)


def test_headers_de_seguranca_presentes_na_resposta(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_headers_presentes_tambem_em_respostas_de_erro(client):
    """Um 401 também é uma resposta que o navegador processa."""
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_hsts_ausente_por_padrao(client):
    """Em http://localhost, HSTS travaria o próprio ambiente de desenvolvimento."""
    assert "Strict-Transport-Security" not in client.get("/api/health").headers


def test_hsts_presente_quando_habilitado():
    mini = FastAPI()
    mini.add_middleware(
        SecurityHeadersMiddleware, hsts_enabled=True, hsts_max_age=31536000
    )

    @mini.get("/ping")
    def ping():
        return {"ok": True}

    r = TestClient(mini).get("/ping")
    assert r.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )
