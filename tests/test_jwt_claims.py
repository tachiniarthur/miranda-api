"""
Testes das claims `iss` e `aud` do JWT (item #15 da revisão de segurança).

Sem elas, qualquer token assinado com a MESMA chave — por outro serviço da
stack, por um ambiente de staging que herdou a chave — seria aceito por esta
API sem questionamento. Com elas, o token precisa dizer quem o emitiu e para
quem vale.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import create_access_token, decode_token

USER_ID = "11111111-1111-1111-1111-111111111111"


def _forjar(**claims) -> str:
    """Token assinado com a chave REAL — só as claims mudam."""
    payload = {
        "sub": USER_ID,
        "type": "access",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    payload.update(claims)
    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def _claims(token: str) -> dict:
    return jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
    )


# ── Emissão ───────────────────────────────────────────────────────────

def test_token_emitido_carrega_iss_e_aud():
    c = _claims(create_access_token(USER_ID))
    assert c["iss"] == settings.JWT_ISSUER
    assert c["aud"] == settings.JWT_AUDIENCE


def test_valores_padrao_identificam_a_aplicacao():
    assert settings.JWT_ISSUER == "miranda-api"
    assert settings.JWT_AUDIENCE == "miranda-app"


def test_token_proprio_continua_valido():
    assert decode_token(create_access_token(USER_ID), "access") == USER_ID


# ── Validação ─────────────────────────────────────────────────────────

def test_token_sem_as_claims_e_recusado():
    """Formato dos tokens emitidos ANTES desta mudança: deixam de valer."""
    assert decode_token(_forjar(), "access") is None


def test_token_com_issuer_errado_e_recusado():
    forjado = _forjar(iss="outro-servico", aud=settings.JWT_AUDIENCE)
    assert decode_token(forjado, "access") is None


def test_token_com_audience_errada_e_recusado():
    forjado = _forjar(iss=settings.JWT_ISSUER, aud="outro-app")
    assert decode_token(forjado, "access") is None


def test_token_sem_audience_e_recusado():
    assert decode_token(_forjar(iss=settings.JWT_ISSUER), "access") is None


def test_token_sem_issuer_e_recusado():
    assert decode_token(_forjar(aud=settings.JWT_AUDIENCE), "access") is None


# ── As proteções que já existiam continuam de pé ──────────────────────

def test_assinatura_invalida_continua_recusada():
    adulterado = create_access_token(USER_ID)[:-4] + "AAAA"
    assert decode_token(adulterado, "access") is None


def test_token_expirado_continua_recusado():
    expirado = _forjar(
        iss=settings.JWT_ISSUER,
        aud=settings.JWT_AUDIENCE,
        exp=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    assert decode_token(expirado, "access") is None


@pytest.mark.parametrize("tipo_errado", ["password_reset", "refresh", ""])
def test_tipo_de_token_divergente_continua_recusado(tipo_errado):
    assert decode_token(create_access_token(USER_ID), tipo_errado) is None
