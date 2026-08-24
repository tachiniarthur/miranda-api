"""
Testes da camada de e-mail.

A regra central deste módulo é uma só: **enviar e-mail nunca pode derrubar a
operação que o disparou**. Um servidor SMTP fora do ar não pode impedir alguém
de se cadastrar, e uma chave de API errada não pode virar HTTP 500 numa rota de
autenticação. Por isso `send_email` devolve um booleano em vez de lançar, e a
maior parte destes testes exercita caminhos de falha.
"""

import logging

import pytest

from app.services.email import sender
from app.services.email.messages import (
    render_duplicate_signup_notice,
    render_email_verification,
    render_password_reset,
)
from app.services.email.sender import EmailMessage, send_email


@pytest.fixture
def msg() -> EmailMessage:
    return EmailMessage(to="alguem@exemplo.com", subject="Assunto", text="Corpo")


def _backend(monkeypatch, name: str, **extra):
    monkeypatch.setattr(sender.settings, "EMAIL_BACKEND", name)
    monkeypatch.setattr(sender.settings, "EMAIL_FROM", "miranda@localhost")
    for k, v in extra.items():
        monkeypatch.setattr(sender.settings, k, v)


# ── Backend console: o padrão, e o que roda em teste ────────────────────────
def test_console_backend_logs_instead_of_sending(monkeypatch, msg, caplog):
    _backend(monkeypatch, "console")
    with caplog.at_level(logging.INFO, logger="miranda.email"):
        assert send_email(msg) is True
    assert "alguem@exemplo.com" in caplog.text
    assert "Assunto" in caplog.text


# ── Backend SMTP ────────────────────────────────────────────────────────────
class _FakeSMTP:
    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, m):
        self.sent.append(m)


def test_smtp_backend_sends_through_the_configured_host(monkeypatch, msg):
    _FakeSMTP.instances.clear()
    _backend(monkeypatch, "smtp", SMTP_HOST="localhost", SMTP_PORT=1025)
    monkeypatch.setattr(sender.smtplib, "SMTP", _FakeSMTP)

    assert send_email(msg) is True
    sent = _FakeSMTP.instances[0].sent[0]
    assert sent["To"] == "alguem@exemplo.com"
    assert sent["Subject"] == "Assunto"
    assert sent["From"] == "miranda@localhost"


def test_smtp_failure_is_swallowed_and_logged(monkeypatch, msg, caplog):
    """
    Mailpit fora do ar não pode impedir um cadastro. O retorno vira False e o
    log registra — quem chamou decide o que fazer, e a resposta ao usuário
    continua sendo a mesma.
    """
    def _boom(*a, **k):
        raise OSError("connection refused")

    _backend(monkeypatch, "smtp", SMTP_HOST="localhost", SMTP_PORT=1025)
    monkeypatch.setattr(sender.smtplib, "SMTP", _boom)

    with caplog.at_level(logging.WARNING, logger="miranda.email"):
        assert send_email(msg) is False
    assert "connection refused" in caplog.text


def test_a_malformed_display_name_cannot_take_down_a_signup(monkeypatch, caplog):
    """
    Um nome de exibição digitado no cadastro (`user.name`) entra na mensagem
    via f-string, sem validação nenhuma — diferente do endereço, que passa por
    `email-validator`. Um surrogate solto (ex.: metade de um emoji corrompido)
    faz `EmailMessage.set_content` levantar `UnicodeEncodeError`. Isso não pode
    virar HTTP 500 na rota de reset de senha.
    """
    bad_msg = EmailMessage(to="alguem@exemplo.com", subject="Assunto", text="Olá " + chr(0xD800))
    _backend(monkeypatch, "smtp", SMTP_HOST="localhost", SMTP_PORT=1025)

    with caplog.at_level(logging.WARNING, logger="miranda.email"):
        assert send_email(bad_msg) is False


def test_a_linefeed_in_the_recipient_cannot_take_down_a_signup(monkeypatch, caplog):
    """
    Mesma classe de bug, outro campo: um `to` com quebra de linha (injeção de
    cabeçalho) faz a atribuição do cabeçalho MIME levantar `ValueError`. Isso
    também não pode escapar de `send_email`.
    """
    bad_msg = EmailMessage(to="alguem@exemplo.com\nBcc: outro@exemplo.com", subject="Assunto", text="Corpo")
    _backend(monkeypatch, "smtp", SMTP_HOST="localhost", SMTP_PORT=1025)

    with caplog.at_level(logging.WARNING, logger="miranda.email"):
        assert send_email(bad_msg) is False


# ── Backend Resend ──────────────────────────────────────────────────────────
def test_resend_backend_posts_to_the_api(monkeypatch, msg):
    captured = {}

    class _Resp:
        status_code = 200
        text = "{}"

    def _post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, json=json, headers=headers)
        return _Resp()

    _backend(monkeypatch, "resend", RESEND_API_KEY="re_chave_de_teste")
    monkeypatch.setattr(sender.httpx, "post", _post)

    assert send_email(msg) is True
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_chave_de_teste"
    assert captured["json"]["to"] == ["alguem@exemplo.com"]


def test_resend_without_a_key_does_not_call_the_network(monkeypatch, msg):
    called = False

    def _post(*a, **k):
        nonlocal called
        called = True

    _backend(monkeypatch, "resend", RESEND_API_KEY="")
    monkeypatch.setattr(sender.httpx, "post", _post)

    assert send_email(msg) is False
    assert called is False


def test_resend_error_status_is_a_failure_not_an_exception(monkeypatch, msg):
    class _Resp:
        status_code = 422
        text = '{"message":"domain not verified"}'

    _backend(monkeypatch, "resend", RESEND_API_KEY="re_x")
    monkeypatch.setattr(sender.httpx, "post", lambda *a, **k: _Resp())
    assert send_email(msg) is False


def test_an_unreadable_resend_response_body_cannot_take_down_a_signup(monkeypatch, msg):
    """
    `.text` decodifica o corpo da resposta na hora em que é lido — fora do
    try/except que cerca `httpx.post`. Um corpo mal-formado (encoding
    inconsistente com o `Content-Type` declarado, por exemplo) faz a leitura
    levantar. Mesma regra do módulo inteiro: isso vira False, não exceção.
    """
    class _Resp:
        status_code = 422

        @property
        def text(self):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    _backend(monkeypatch, "resend", RESEND_API_KEY="re_x")
    monkeypatch.setattr(sender.httpx, "post", lambda *a, **k: _Resp())
    assert send_email(msg) is False


def test_an_unknown_backend_fails_closed(monkeypatch, msg):
    """Backend digitado errado não pode virar envio silencioso nem exceção."""
    _backend(monkeypatch, "smpt")  # typo proposital
    assert send_email(msg) is False


# ── Conteúdo das mensagens ──────────────────────────────────────────────────
def test_password_reset_message_carries_the_url():
    m = render_password_reset("Arthur", "http://localhost:3000/reset?token=abc")
    assert m.subject
    assert "http://localhost:3000/reset?token=abc" in m.text
    assert "Arthur" in m.text


def test_verification_message_carries_the_url():
    m = render_email_verification("Arthur", "http://localhost:3000/verificar?token=xyz")
    assert "http://localhost:3000/verificar?token=xyz" in m.text


def test_duplicate_signup_notice_never_contains_a_password_or_token():
    """
    Este e-mail vai para quem JÁ tem conta, avisando de uma tentativa de
    cadastro com o mesmo endereço. Ele não pode carregar nada acionável: quem
    disparou a tentativa pode não ser o dono.
    """
    m = render_duplicate_signup_notice("Arthur")
    lowered = m.text.lower()
    assert "senha" not in lowered or "sua senha" in lowered
    assert "token" not in lowered
    assert "http" not in lowered or "/forgot" in lowered
