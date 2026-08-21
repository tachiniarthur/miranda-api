"""
Testes da política de senha (item #14 da revisão de segurança).

O `min_length=8` já existia e garante comprimento; o que se acrescenta aqui é a
recusa das senhas triviais, que passam no comprimento e são os primeiros
palpites de qualquer ataque de dicionário.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.common_passwords import (
    COMMON_PASSWORDS,
    COMMON_PASSWORD_MESSAGE,
    is_common_password,
)
from app.schemas.auth import RegisterRequest, ResetPasswordRequest

SENHA_BOA = "cavalo-bateria-grampo-9"


def _register(password: str) -> RegisterRequest:
    return RegisterRequest(name="Fulana", email="fulana@exemplo.com", password=password)


# ── A lista em si ─────────────────────────────────────────────────────

def test_senhas_obvias_estao_na_lista():
    for senha in ("senha123", "password", "12345678", "qwerty123", "admin123"):
        assert is_common_password(senha), senha


def test_comparacao_ignora_caixa_e_espacos():
    assert is_common_password("Senha123")
    assert is_common_password("  PASSWORD  ")


def test_senha_forte_nao_esta_na_lista():
    assert not is_common_password(SENHA_BOA)


def test_lista_nao_tem_entradas_duplicadas_por_caixa():
    """Tudo em minúsculas: uma entrada capitalizada nunca seria alcançada."""
    assert all(s == s.lower().strip() for s in COMMON_PASSWORDS)


# ── Cadastro ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("senha", ["senha123", "password123", "12345678", "miranda123"])
def test_cadastro_recusa_senha_comum(senha):
    with pytest.raises(ValidationError) as exc:
        _register(senha)
    assert COMMON_PASSWORD_MESSAGE in str(exc.value)


def test_cadastro_aceita_senha_forte():
    assert _register(SENHA_BOA).password == SENHA_BOA


def test_senha_curta_continua_recusada_pelo_min_length():
    """A regra nova não substituiu a antiga."""
    with pytest.raises(ValidationError):
        _register("abc123")


# ── Troca de senha ────────────────────────────────────────────────────

def test_troca_de_senha_recusa_senha_comum():
    """De nada adiantaria barrar no cadastro e liberar no reset."""
    with pytest.raises(ValidationError) as exc:
        ResetPasswordRequest(token="qualquer", new_password="senha123")
    assert COMMON_PASSWORD_MESSAGE in str(exc.value)


def test_troca_de_senha_aceita_senha_forte():
    assert ResetPasswordRequest(token="t", new_password=SENHA_BOA).new_password == (
        SENHA_BOA
    )


def test_mensagem_de_erro_explica_o_motivo():
    """Mensagem acionável: o usuário precisa saber o que fazer diferente."""
    with pytest.raises(ValidationError) as exc:
        _register("password")
    texto = str(exc.value)
    assert "conhecida demais" in texto
    assert "Escolha outra" in texto
