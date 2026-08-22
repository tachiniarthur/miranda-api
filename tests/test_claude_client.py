"""
Testes do transporte. Nenhum toca a rede: o SDK é substituído por dublês.

O que importa aqui é a CLASSIFICAÇÃO do erro — decidir o que merece nova
tentativa e o que não merece é a única regra de verdade deste módulo, e errar
nela custa dinheiro (retentar um 400 três vezes) ou disponibilidade (desistir de
um 429 na primeira).
"""

import logging

import anthropic
import httpx
import pytest

from app.services.ai import claude_client
from app.services.ai.claude_client import (
    ClaudeUsage,
    LookApiFatal,
    LookApiTransient,
    request_composition,
)


class _Usage:
    def __init__(self, i=100, o=200):
        self.input_tokens = i
        self.output_tokens = o


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Response:
    def __init__(self, text='{"looks":[],"note":null}', usage=None):
        self.content = [_Block(text)]
        self.usage = usage or _Usage()
        self.stop_reason = "end_turn"


class _FakeMessages:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeClient:
    def __init__(self, outcome):
        self.messages = _FakeMessages(outcome)


@pytest.fixture(autouse=True)
def _clear_cache():
    claude_client.reset_client_cache()
    yield
    claude_client.reset_client_cache()


def _install(monkeypatch, outcome, api_key="sk-ant-test"):
    fake = _FakeClient(outcome)
    monkeypatch.setattr(claude_client.settings, "ANTHROPIC_API_KEY", api_key)
    monkeypatch.setattr(claude_client, "_build_client", lambda: fake)
    return fake


def _api_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": "x"}})
    return anthropic.APIStatusError("boom", response=response, body=None)


def _typed_api_error(cls: type, status: int) -> anthropic.APIStatusError:
    """
    Constrói a subclasse TIPADA (`RateLimitError`, `NotFoundError`, ...) que o
    SDK de verdade levanta quando é ele mesmo quem monta o erro a partir de uma
    resposta HTTP real (`_make_status_error` promove por status code). Isso é
    diferente de `_api_error()` acima, que instancia `APIStatusError` genérico
    à mão — o formato que um erro construído diretamente tem. Os dois cenários
    existem na suíte de propósito: um prova o caminho de produção (as cláusulas
    `except` tipadas), o outro prova o fallback genérico. Nenhum dos dois é
    redundante com o outro.
    """
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": "x"}})
    return cls("boom", response=response, body=None)


# ── Caminho feliz ───────────────────────────────────────────────────────────
def test_returns_the_text_of_the_first_text_block(monkeypatch):
    _install(monkeypatch, _Response(text='{"looks":[]}'))
    reply = request_composition("sys", "user", {"type": "object"})
    assert reply.text == '{"looks":[]}'


def test_reports_token_usage(monkeypatch):
    _install(monkeypatch, _Response(usage=_Usage(1234, 567)))
    reply = request_composition("sys", "user", {"type": "object"})
    assert reply.usage.input_tokens == 1234
    assert reply.usage.output_tokens == 567


def test_sends_effort_and_schema_but_never_temperature(monkeypatch):
    """
    `temperature` retorna HTTP 400 nos modelos atuais. Este teste é a trava
    que impede alguém de "restaurar" o parâmetro achando que foi esquecido.
    """
    schema = {"type": "object", "properties": {}}
    fake = _install(monkeypatch, _Response())
    request_composition("sys", "user", schema)

    sent = fake.messages.calls[0]
    assert "temperature" not in sent
    assert sent["output_config"]["effort"] == claude_client.settings.ANTHROPIC_EFFORT
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert sent["output_config"]["format"]["schema"] is schema
    assert sent["system"] == "sys"
    assert sent["messages"] == [{"role": "user", "content": "user"}]


def test_logs_tokens_and_estimated_cost(monkeypatch, caplog):
    _install(monkeypatch, _Response(usage=_Usage(1_000_000, 1_000_000)))
    with caplog.at_level(logging.INFO, logger="miranda.ai.claude_client"):
        request_composition("sys", "user", {"type": "object"})
    logged = caplog.text
    assert "input_tokens=1000000" in logged
    assert "output_tokens=1000000" in logged
    assert "30.0" in logged  # US$ 5 de entrada + US$ 25 de saída


# ── Classificação de erro ───────────────────────────────────────────────────
def test_missing_key_is_fatal_and_never_touches_the_network(monkeypatch):
    fake = _install(monkeypatch, _Response(), api_key="")
    with pytest.raises(LookApiFatal):
        request_composition("sys", "user", {"type": "object"})
    assert fake.messages.calls == []


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 529])
def test_rate_limit_and_server_errors_are_transient(monkeypatch, status):
    _install(monkeypatch, _api_error(status))
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


def test_connection_errors_are_transient(monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    _install(monkeypatch, anthropic.APIConnectionError(request=request))
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_fatal(monkeypatch, status):
    _install(monkeypatch, _api_error(status))
    with pytest.raises(LookApiFatal):
        request_composition("sys", "user", {"type": "object"})


# ── Classificação de erro — subclasses TIPADAS ──────────────────────────────
# Os testes acima usam `_api_error()`, que constrói um `APIStatusError`
# genérico à mão — é o formato de um erro montado diretamente, não o que a API
# de verdade produz. Em produção, o SDK promove por status code e levanta a
# subclasse TIPADA (`RateLimitError`, `NotFoundError`, ...). Sem testar esse
# caminho, as cláusulas `except` tipadas em `request_composition` nunca são
# exercitadas — o requisito de retentar em rate limit ficaria sem prova real.
# Os dois conjuntos de teste cobrem caminhos diferentes; nenhum é redundante.
def test_typed_rate_limit_error_is_transient(monkeypatch):
    _install(monkeypatch, _typed_api_error(anthropic.RateLimitError, 429))
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


@pytest.mark.parametrize(
    "cls,status",
    [
        (anthropic.AuthenticationError, 401),
        (anthropic.PermissionDeniedError, 403),
        (anthropic.NotFoundError, 404),
        (anthropic.BadRequestError, 400),
    ],
)
def test_typed_client_errors_are_fatal(monkeypatch, cls, status):
    _install(monkeypatch, _typed_api_error(cls, status))
    with pytest.raises(LookApiFatal):
        request_composition("sys", "user", {"type": "object"})


def test_a_response_without_text_is_transient(monkeypatch):
    """
    Resposta vazia com `stop_reason` que NÃO é `max_tokens` nem `refusal` (ex.:
    um `end_turn` sem bloco de texto, uma anomalia do SDK) ainda é tratada como
    transitória — os dois casos determinísticos têm cláusula própria acima e
    são fatais, não retentáveis (ver os testes logo abaixo).
    """
    response = _Response()
    response.content = []
    _install(monkeypatch, response)
    with pytest.raises(LookApiTransient):
        request_composition("sys", "user", {"type": "object"})


# ── stop_reason determinístico: max_tokens e refusal são FATAIS ────────────
# Retentar uma resposta cortada por `max_tokens` ou uma recusa gasta a chamada
# inteira (até o teto de ANTHROPIC_MAX_OUTPUT_TOKENS) três vezes sem chance de
# um resultado diferente — os dois precisam desistir na primeira tentativa.
def test_max_tokens_stop_reason_is_fatal_not_retried(monkeypatch):
    response = _Response(text="")
    response.stop_reason = "max_tokens"
    _install(monkeypatch, response)
    with pytest.raises(LookApiFatal, match="ANTHROPIC_MAX_OUTPUT_TOKENS"):
        request_composition("sys", "user", {"type": "object"})


def test_refusal_stop_reason_is_fatal_not_retried(monkeypatch):
    response = _Response(text="")
    response.content = []  # recusa: HTTP 200 sem bloco de texto
    response.stop_reason = "refusal"
    _install(monkeypatch, response)
    with pytest.raises(LookApiFatal, match="refusal"):
        request_composition("sys", "user", {"type": "object"})


# ── Custo estimado ──────────────────────────────────────────────────────────
def test_cost_uses_the_price_table_of_the_model():
    usage = ClaudeUsage(input_tokens=200_000, output_tokens=40_000, model="claude-opus-5")
    # 0,2 MTok × US$5 + 0,04 MTok × US$25 = 1,00 + 1,00
    assert usage.estimated_cost_usd == pytest.approx(2.0)


def test_cost_of_an_unknown_model_is_zero_not_a_crash():
    """
    Trocar ANTHROPIC_MODEL não pode derrubar a geração só porque o preço do
    modelo novo ainda não foi cadastrado. O custo vira 0.0 e o log avisa.
    """
    usage = ClaudeUsage(input_tokens=1000, output_tokens=1000, model="modelo-inexistente")
    assert usage.estimated_cost_usd == 0.0
