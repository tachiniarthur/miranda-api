"""
A configuração da API do Claude tem uma regra própria: a aplicação DEVE subir
sem a chave.

Isso é deliberado e diferente do JWT_SECRET_KEY, que derruba o boot quando é
fraco. Uma chave de JWT ausente torna a autenticação forjável em silêncio; uma
chave da Anthropic ausente apenas desliga a geração de look, que já sabe
degradar com uma mensagem clara. Derrubar a API inteira por isso trocaria uma
funcionalidade quebrada por um serviço fora do ar.
"""

from app.core.config import Settings


def _settings(**overrides) -> Settings:
    """Instancia Settings sem depender do .env real da máquina."""
    base = {
        "DATABASE_URL": "postgresql+psycopg2://u:p@localhost:5432/db",
        "JWT_SECRET_KEY": "x" * 48,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_boots_without_an_anthropic_key():
    s = _settings()
    assert s.ANTHROPIC_API_KEY == ""


def test_model_defaults_to_opus_5():
    s = _settings()
    assert s.ANTHROPIC_MODEL == "claude-opus-5"


def test_model_can_be_swapped_without_touching_code():
    s = _settings(ANTHROPIC_MODEL="claude-sonnet-5")
    assert s.ANTHROPIC_MODEL == "claude-sonnet-5"


def test_call_budget_defaults_are_conservative():
    s = _settings()
    assert s.ANTHROPIC_MAX_OUTPUT_TOKENS == 4000
    assert s.ANTHROPIC_EFFORT == "medium"
    assert s.ANTHROPIC_MAX_ATTEMPTS == 3
