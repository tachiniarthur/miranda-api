"""
Storage do rate limiter.

Em memória, cada worker teria a própria cota e o teto valeria N vezes mais que
o configurado. Estes testes protegem a configuração e, sobretudo, a degradação:
Redis indisponível não pode derrubar a API inteira.
"""

import logging

import pytest

from app.core import rate_limit
from app.core.config import Settings


def _settings(**over) -> Settings:
    base = {
        "DATABASE_URL": "postgresql+psycopg2://u:p@localhost:5432/db",
        "JWT_SECRET_KEY": "x" * 48,
        "_env_file": None,
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def test_o_padrao_aponta_para_o_redis_local():
    assert _settings().RATE_LIMIT_STORAGE_URI == "redis://localhost:6379/0"


def test_a_uri_e_configuravel():
    s = _settings(RATE_LIMIT_STORAGE_URI="redis://outro-host:6379/3")
    assert s.RATE_LIMIT_STORAGE_URI == "redis://outro-host:6379/3"


def test_memoria_continua_valendo_para_maquina_de_um_worker():
    s = _settings(RATE_LIMIT_STORAGE_URI="memory://")
    assert s.RATE_LIMIT_STORAGE_URI == "memory://"


def test_redis_inacessivel_cai_para_memoria_em_vez_de_quebrar(caplog):
    """
    Redis fora do ar degrada o rate limit, não a aplicação. Um teto por worker
    é pior que um teto global — mas é infinitamente melhor que HTTP 500 em toda
    rota de autenticação.
    """
    with caplog.at_level(logging.WARNING, logger="miranda.rate_limit"):
        uri = rate_limit.resolve_storage_uri("redis://127.0.0.1:1/0")
    assert uri == "memory://"
    assert "memory" in caplog.text.lower()


def test_redis_acessivel_e_usado_como_esta():
    """Exige o Redis do docker compose; sem ele, o teste é pulado."""
    import redis

    try:
        redis.Redis.from_url(
            "redis://localhost:6379/0", socket_connect_timeout=1
        ).ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis indisponível — teste pulado ({type(exc).__name__}).")

    assert rate_limit.resolve_storage_uri("redis://localhost:6379/0") == (
        "redis://localhost:6379/0"
    )
