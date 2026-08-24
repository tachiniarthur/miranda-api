# Pendências pré-produção — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar as cinco frentes de pendência conhecida do Miranda, de modo que a lista do que resta contenha apenas itens que dependem do ambiente real de hospedagem.

**Architecture:** Duas dependências locais novas sobem numa única stack de Docker Compose (Mailpit para capturar e-mail, Redis para o rate limiter). Uma camada `EmailSender` com backends trocáveis por variável de ambiente isola o envio, e é sobre ela que a verificação de e-mail e o fechamento da enumeração de conta são construídos. A autenticação migra do header `Authorization` para cookie `httpOnly`, o que remove o token do alcance do JavaScript e apaga a necessidade do `AuthedImage`. As proteções de abuso (quota, rate limit, hash perceptual) entram na borda das rotas de guarda-roupa.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2, slowapi, Redis 7, Mailpit, Next.js 14, Pillow, pytest 9.

**Spec:** `docs/superpowers/specs/2026-08-24-pendencias-pre-producao.md`

## Global Constraints

- **Ordem é obrigatória.** A Frente 3 (e-mail) vem primeiro: a Frente 4 depende dela. O resto segue a ordem das tarefas.
- **A aplicação sobe sem nada opcional.** Sem Mailpit, sem Redis e sem `ANTHROPIC_API_KEY`, a API precisa iniciar e servir. Cada dependência ausente degrada só a sua função, nunca o boot. O único segredo que derruba o boot continua sendo `JWT_SECRET_KEY` fraco.
- **Nunca vaze existência de conta.** Nenhuma resposta, código de status ou diferença de tempo pode revelar se um e-mail está cadastrado.
- **Nenhum segredo em arquivo versionado.** `.env` é gitignored; `.env.example` recebe só placeholders. Nada de `sk-ant-`, `re_`, senha ou chave real.
- **Comentários e mensagens de usuário em português**, explicando o *porquê*. O código existente é assim — siga.
- **A análise de peça não muda de arquitetura.** `clothing_analysis.py`, `fashion_clip.py`, `color_extraction.py`, `rules.py` só mudam se a tarefa disser. `labels.py` muda apenas nos prompts de vestido/saia.
- **Migrations são incrementais e nomeadas em sequência**: as próximas são `0005_`, `0006_`. Nunca edite uma migration já aplicada.
- **Baseline da suíte no início: 216 passed, 2 skipped.** Nenhuma tarefa pode regredir isso; toda tarefa reporta o número antes e depois.
- **Comando Python:** os shebangs da venv estão quebrados (a pasta do projeto foi movida). Use SEMPRE `.venv/bin/python -m <módulo>`, nunca `.venv/bin/<script>`.
- **Não rode `scripts/validate_look_live.py` nem os testes marcados `live`** — custam dinheiro real.
- **`ENABLE_PROMPT_CACHE` continua `false`.** Não é escopo desta rodada.
- **O README vive em `/home/tachas/projects/my/miranda-folder/README.md`, FORA de qualquer repositório git.** Não pode ser commitado e não tem desfazer: localize edições por conteúdo, nunca por número de linha, e faça backup antes de reescrever blocos grandes.

---

## Ordem das tarefas

| # | Frente | Entrega |
|---|---|---|
| 1 | 3 | Stack local: Docker Compose com Mailpit e Redis |
| 2 | 3 | Camada `EmailSender` (console/smtp/resend) |
| 3 | 3 | Reset de senha passa a enviar e-mail de verdade (fecha item #1) |
| 4 | 4 | Modelo e migration: verificação de e-mail |
| 5 | 4 | Fluxo de verificação: envio, confirmação, reenvio |
| 6 | 4 | Cadastro genérico + aviso ao dono da conta (fecha item #4) |
| 7 | 5 | Rate limiter no Redis |
| 8 | 5 | Cookie httpOnly — backend |
| 9 | 5 | Cookie httpOnly — frontend |
| 10 | 2 | Quota de peças por usuário |
| 11 | 2 | Rate limit em upload e `/analyze` |
| 12 | 2 | Hash perceptual anti-reenvio |
| 13 | 1 | Investigação vestido/saia e calibração |
| 14 | 1 | Segunda camada de defesa e limitação documentada |
| 15 | — | README: instalação, checklist de deploy, pendências |

---
### Task 1: Stack local — Mailpit e Redis via Docker Compose

**Files:**
- Create: `docker-compose.yml`
- Test: nenhum automatizado — é infraestrutura. A verificação é o Step 4.

**Interfaces:**
- Consumes: nada.
- Produces: Mailpit em `localhost:1025` (SMTP) e `localhost:8025` (interface web); Redis em `localhost:6379`.

**Contexto:** Docker 29.7.2 e Compose v5.5.0 já estão instalados e funcionando. Redis e Mailpit não estão instalados nativamente. Nenhum dos dois é obrigatório para a API subir — são dependências que degradam graciosamente (Tasks 2 e 7 garantem isso).

- [ ] **Step 1: Criar `docker-compose.yml` na raiz de `miranda-api/`**

```yaml
# Dependências locais de desenvolvimento do Miranda API.
#
# Nenhuma das duas é obrigatória para a API subir: sem Mailpit o envio de e-mail
# cai no backend `console` (imprime no log), sem Redis o rate limiter volta para
# memória. Elas existem para que o comportamento local seja o mesmo de produção,
# não para que a aplicação dependa de Docker.
#
#   docker compose up -d     # sobe as duas
#   docker compose down      # derruba
#   docker compose ps        # confere
services:
  # Captura de e-mail para desenvolvimento. NÃO envia nada para fora: tudo que a
  # aplicação "manda" fica numa caixa de entrada web em http://localhost:8025.
  # É o que permite testar o fluxo de verificação sem conta em serviço nenhum e
  # sem expor endereço real.
  mailpit:
    image: axllent/mailpit:latest
    container_name: miranda-mailpit
    restart: unless-stopped
    ports:
      - "1025:1025"   # SMTP — é para cá que a API envia
      - "8025:8025"   # interface web — é aqui que você lê
    environment:
      MP_MAX_MESSAGES: 500
      MP_SMTP_AUTH_ACCEPT_ANY: 1
      MP_SMTP_AUTH_ALLOW_INSECURE: 1

  # Storage do rate limiter. Em memória, cada worker do uvicorn teria a própria
  # cota — com 4 workers, um teto de 5 tentativas vira 20 na prática.
  redis:
    image: redis:7-alpine
    container_name: miranda-redis
    restart: unless-stopped
    ports:
      - "6379:6379"
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    # Sem persistência de propósito: o que vive aqui são contadores de rate
    # limit, que podem (e devem) morrer junto com o contêiner.
```

- [ ] **Step 2: Subir a stack**

Run: `docker compose up -d`
Expected: dois contêineres criados, `miranda-mailpit` e `miranda-redis`.

- [ ] **Step 3: Verificar que os dois responderam**

Run:
```bash
docker compose ps
curl -s -o /dev/null -w "mailpit web: %{http_code}\n" http://localhost:8025
docker exec miranda-redis redis-cli ping
```
Expected: ambos `running`; `mailpit web: 200`; `PONG`.

- [ ] **Step 4: Provar que o SMTP do Mailpit aceita mensagem**

Run:
```bash
.venv/bin/python - <<'EOF'
import smtplib
from email.message import EmailMessage
m = EmailMessage()
m["From"] = "miranda@localhost"; m["To"] = "teste@localhost"
m["Subject"] = "fumaça"; m.set_content("se você está lendo isto no Mailpit, o SMTP funciona")
with smtplib.SMTP("localhost", 1025) as s:
    s.send_message(m)
print("enviado")
EOF
curl -s http://localhost:8025/api/v1/messages | head -c 200
```
Expected: `enviado`, e o JSON do Mailpit listando a mensagem com o assunto "fumaça".

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: stack local de desenvolvimento com Mailpit e Redis"
```

---

### Task 2: Camada de envio de e-mail

**Files:**
- Create: `app/services/email/__init__.py`
- Create: `app/services/email/sender.py`
- Create: `app/services/email/messages.py`
- Modify: `app/core/config.py` (bloco novo depois de `ENABLE_PROMPT_CACHE`)
- Modify: `.env.example`
- Test: `tests/test_email_sender.py`

**Interfaces:**
- Consumes: `settings` (Task 1 não produz interface de código).
- Produces:
  - `class EmailMessage` — dataclass com `to: str`, `subject: str`, `text: str`.
  - `def send_email(message: EmailMessage) -> bool` — nunca lança; devolve `True` se entregou.
  - `def render_password_reset(name: str, reset_url: str) -> EmailMessage`
  - `def render_email_verification(name: str, verify_url: str) -> EmailMessage`
  - `def render_duplicate_signup_notice(name: str) -> EmailMessage`
  - `settings.EMAIL_BACKEND`, `EMAIL_FROM`, `SMTP_HOST`, `SMTP_PORT`, `RESEND_API_KEY`, `APP_BASE_URL`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_email_sender.py`:

```python
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_email_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.email'`

- [ ] **Step 3: Adicionar a configuração em `app/core/config.py`**

Insira depois de `ENABLE_PROMPT_CACHE: bool = False`:

```python
    # ── Envio de e-mail ───────────────────────────────────────────────
    # Três backends, trocados por variável — nenhum código muda ao hospedar:
    #   console — só registra no log. Padrão, e o que roda nos testes.
    #   smtp    — Mailpit local (docker compose up -d). Captura tudo numa caixa
    #             web em http://localhost:8025 e NÃO envia nada para fora.
    #   resend  — serviço transacional real. Exige RESEND_API_KEY e, para
    #             enviar a endereços que não sejam o seu, um domínio verificado.
    #
    # O padrão é `console` de propósito: a aplicação precisa subir e funcionar
    # numa máquina sem Docker e sem conta em serviço nenhum.
    EMAIL_BACKEND: str = "console"
    EMAIL_FROM: str = "Miranda <nao-responda@miranda.local>"

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025

    RESEND_API_KEY: str = ""

    # Base das URLs que vão DENTRO dos e-mails (o frontend, não a API). É o que
    # o usuário clica, então precisa ser o endereço público do app.
    APP_BASE_URL: str = "http://localhost:3000"
```

- [ ] **Step 4: Criar `app/services/email/__init__.py`**

```python
"""
Envio de e-mail transacional.

Dois arquivos com responsabilidades separadas de propósito: `sender.py` sabe
COMO entregar (SMTP, Resend, log) e `messages.py` sabe O QUE dizer. O conteúdo
muda por motivo de produto; o transporte muda por motivo de infraestrutura.
"""
```

- [ ] **Step 5: Criar `app/services/email/sender.py`**

```python
"""
Transporte de e-mail — três backends, um contrato.

── A regra que governa este módulo ─────────────────────────────────────────
`send_email` NUNCA lança. Devolve `True` se entregou e `False` se não. Isso é
deliberado: enviar e-mail é sempre um efeito colateral de outra coisa (um
cadastro, um pedido de reset), e o efeito colateral não pode derrubar a
operação principal. Um Mailpit desligado não pode impedir alguém de criar
conta, e uma chave de Resend errada não pode virar HTTP 500 numa rota de auth.

Quem chama decide o que fazer com o `False` — e, nas rotas de autenticação, a
decisão é sempre "responder a mesma coisa de qualquer jeito", porque a resposta
não pode variar conforme a entrega (senão vira canal de enumeração de contas).
"""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as _MIMEMessage

import httpx

from app.core.config import settings

logger = logging.getLogger("miranda.email")

RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class EmailMessage:
    """Uma mensagem pronta para envio. Só texto: nada de HTML nesta fase."""

    to: str
    subject: str
    text: str


def _send_console(message: EmailMessage) -> bool:
    """
    Backend padrão: registra no log em vez de enviar.

    Existe para a aplicação funcionar numa máquina sem Docker e sem conta em
    serviço nenhum. É também o backend dos testes — nenhuma suíte deve depender
    de rede.
    """
    logger.info(
        "[e-mail não enviado: backend=console] para=%s assunto=%s\n%s",
        message.to,
        message.subject,
        message.text,
    )
    return True


def _send_smtp(message: EmailMessage) -> bool:
    mime = _MIMEMessage()
    mime["From"] = settings.EMAIL_FROM
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime.set_content(message.text)

    try:
        with smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=_TIMEOUT_SECONDS
        ) as smtp:
            smtp.send_message(mime)
    except Exception as exc:  # noqa: BLE001
        # Largo de propósito: smtplib levanta uma família grande (OSError,
        # SMTPException, socket.timeout...) e nenhuma delas justifica derrubar
        # o cadastro de quem está do outro lado.
        logger.warning(
            "Falha ao enviar e-mail por SMTP (%s:%s) para %s: %s",
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            message.to,
            exc,
        )
        return False

    logger.info("E-mail enviado por SMTP para %s: %s", message.to, message.subject)
    return True


def _send_resend(message: EmailMessage) -> bool:
    if not settings.RESEND_API_KEY:
        logger.warning(
            "EMAIL_BACKEND=resend mas RESEND_API_KEY está vazia; nada foi enviado."
        )
        return False

    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            json={
                "from": settings.EMAIL_FROM,
                "to": [message.to],
                "subject": message.subject,
                "text": message.text,
            },
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"},
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falha de rede ao enviar por Resend para %s: %s", message.to, exc)
        return False

    if response.status_code >= 300:
        # O caso mais comum aqui é domínio não verificado, que o Resend recusa
        # com 4xx. A mensagem da API é útil e vai inteira para o log.
        logger.warning(
            "Resend recusou o envio para %s (HTTP %s): %s",
            message.to,
            response.status_code,
            response.text,
        )
        return False

    logger.info("E-mail enviado por Resend para %s: %s", message.to, message.subject)
    return True


_BACKENDS = {
    "console": _send_console,
    "smtp": _send_smtp,
    "resend": _send_resend,
}


def send_email(message: EmailMessage) -> bool:
    """
    Entrega uma mensagem pelo backend configurado. Nunca lança.

    Returns:
        True se o backend confirmou a entrega, False caso contrário.
    """
    backend = _BACKENDS.get(settings.EMAIL_BACKEND)
    if backend is None:
        # Falha fechada: um backend digitado errado não pode virar envio
        # silencioso nem exceção numa rota de autenticação.
        logger.error(
            "EMAIL_BACKEND=%r não existe. Backends válidos: %s. Nada foi enviado.",
            settings.EMAIL_BACKEND,
            ", ".join(sorted(_BACKENDS)),
        )
        return False
    return backend(message)
```

- [ ] **Step 6: Criar `app/services/email/messages.py`**

```python
"""
O conteúdo dos e-mails da Miranda.

Texto puro, sem HTML: o que estas mensagens precisam entregar é um link e uma
instrução, e um corpo de texto atravessa qualquer cliente sem cair em spam por
formatação. O tom é o da casa — direto, sem exclamação, sem simpatia forçada.

⚠️ Nenhuma destas mensagens revela se um endereço está cadastrado para quem NÃO
é o dono do endereço. É o mesmo princípio que rege as rotas de autenticação.
"""

from __future__ import annotations

from app.services.email.sender import EmailMessage

_ASSINATURA = "\n\n—\nMiranda"


def render_password_reset(name: str, reset_url: str) -> EmailMessage:
    return EmailMessage(
        to="",  # preenchido por quem envia
        subject="Redefinição de senha",
        text=(
            f"{name}, alguém pediu para redefinir a senha desta conta.\n\n"
            f"Para escolher uma senha nova, abra:\n{reset_url}\n\n"
            "O link vale por tempo limitado e só pode ser usado uma vez. "
            "Se não foi você, ignore este e-mail: nada muda sem que o link "
            "seja aberto." + _ASSINATURA
        ),
    )


def render_email_verification(name: str, verify_url: str) -> EmailMessage:
    return EmailMessage(
        to="",
        subject="Confirme seu e-mail",
        text=(
            f"{name}, sua conta na Miranda foi criada.\n\n"
            f"Para confirmar que este endereço é seu, abra:\n{verify_url}\n\n"
            "O link vale por tempo limitado." + _ASSINATURA
        ),
    )


def render_duplicate_signup_notice(name: str) -> EmailMessage:
    """
    Vai para quem JÁ tem conta, quando alguém tenta se cadastrar com o mesmo
    endereço.

    Esta mensagem é o que substitui o antigo `409 "e-mail já cadastrado"` na
    resposta da API — o aviso passa a chegar por um canal que só o dono do
    endereço lê. Por isso ela não carrega NADA acionável: nem token, nem link de
    acesso, nem qualquer dado da conta. Quem disparou a tentativa pode não ser o
    dono, e um link aqui viraria a própria ferramenta de invasão que o aviso
    tenta prevenir.
    """
    return EmailMessage(
        to="",
        subject="Tentativa de cadastro com o seu e-mail",
        text=(
            f"{name}, alguém tentou criar uma conta na Miranda usando este "
            "endereço, que já tem conta.\n\n"
            "Nenhuma conta nova foi criada e nada mudou na sua.\n\n"
            "Se foi você e esqueceu que já tinha cadastro, é só entrar "
            "normalmente. Se esqueceu a senha, use a opção de recuperação na "
            "tela de login. Se não foi você, também não é preciso fazer nada."
            + _ASSINATURA
        ),
    )
```

- [ ] **Step 7: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_email_sender.py -v`
Expected: PASS (11 testes)

- [ ] **Step 8: Documentar no `.env.example`**

Acrescente ao final:

```
# ── Envio de e-mail ──────────────────────────────────────────────────
# console — só registra no log. Padrão; funciona sem Docker e sem conta externa.
# smtp    — Mailpit local. Rode `docker compose up -d` e leia em
#           http://localhost:8025. NÃO envia nada para fora da máquina.
# resend  — serviço real, para quando o projeto for hospedado. Exige
#           RESEND_API_KEY e um domínio verificado para enviar a terceiros.
EMAIL_BACKEND=console
EMAIL_FROM=Miranda <nao-responda@miranda.local>

# Usados só quando EMAIL_BACKEND=smtp.
SMTP_HOST=localhost
SMTP_PORT=1025

# Usado só quando EMAIL_BACKEND=resend. Crie em https://resend.com/api-keys
RESEND_API_KEY=

# Base das URLs que vão DENTRO dos e-mails — é o FRONTEND, não a API.
APP_BASE_URL=http://localhost:3000
```

- [ ] **Step 9: Rodar a suíte inteira**

Run: `.venv/bin/python -m pytest -q`
Expected: 227 passed, 2 skipped (216 + 11 novos).

- [ ] **Step 10: Commit**

```bash
git add app/services/email app/core/config.py .env.example tests/test_email_sender.py
git commit -m "feat(email): camada de envio com backends console, smtp e resend"
```

---
### Task 3: Reset de senha por e-mail de verdade (fecha o item #1 da revisão de segurança)

Hoje `forgot-password` grava o token **em texto puro no log do servidor**, com um `TODO` explícito dizendo que isso só é aceitável enquanto a API roda na máquina do desenvolvedor. Esta tarefa fecha isso.

**Files:**
- Modify: `app/api/routes/auth.py` (rota `forgot_password`, linhas ~85-127)
- Modify: `app/services/auth_service.py` (nada além do necessário — ver Step 3)
- Test: `tests/test_password_reset_flow.py` (acrescentar, não substituir)

**Interfaces:**
- Consumes: `send_email`, `render_password_reset`, `settings.APP_BASE_URL` (Task 2).
- Produces: nenhuma interface nova.

- [ ] **Step 1: Escrever os testes que falham**

Acrescente ao FINAL de `tests/test_password_reset_flow.py`:

```python
# ── Entrega do token por e-mail (substitui o log em texto puro) ─────────────
# O token deixou de ir para o log do servidor. Estes testes protegem as duas
# metades disso: que ele agora vai por e-mail, e que ele NÃO vai mais para
# lugar nenhum que não seja a caixa do dono do endereço.
def test_reset_token_is_sent_by_email_not_logged(client, db, caplog):
    import logging

    from app.services.email import sender

    enviados = []
    original = sender.send_email

    def _captura(message):
        enviados.append(message)
        return True

    sender.send_email = _captura
    try:
        user = _novo_usuario(db)
        with caplog.at_level(logging.DEBUG):
            resp = client.post("/api/auth/forgot-password", json={"email": user.email})
    finally:
        sender.send_email = original

    assert resp.status_code == 200
    assert len(enviados) == 1, "um e-mail devia ter sido enviado"
    assert enviados[0].to == user.email

    # O token está no corpo do e-mail...
    import re

    achado = re.search(r"token=([A-Za-z0-9_\-]{20,})", enviados[0].text)
    assert achado, "o e-mail precisa carregar o link com o token"

    # ...e NÃO está em lugar nenhum do log.
    assert achado.group(1) not in caplog.text


def test_no_email_is_sent_for_an_unknown_address(client, db):
    from app.services.email import sender

    enviados = []
    original = sender.send_email
    sender.send_email = lambda m: enviados.append(m) or True
    try:
        resp = client.post(
            "/api/auth/forgot-password", json={"email": "ninguem-aqui@exemplo.com"}
        )
    finally:
        sender.send_email = original

    assert resp.status_code == 200
    assert enviados == [], "não existe conta: não há para quem enviar"


def test_the_response_is_identical_whether_or_not_the_email_exists(client, db):
    """
    A resposta não pode variar com a existência da conta NEM com o sucesso do
    envio — as duas coisas seriam canais de enumeração.
    """
    from app.services.email import sender

    user = _novo_usuario(db)
    original = sender.send_email
    sender.send_email = lambda m: False  # simula servidor de e-mail fora do ar
    try:
        existe = client.post("/api/auth/forgot-password", json={"email": user.email})
        nao_existe = client.post(
            "/api/auth/forgot-password", json={"email": "ninguem@exemplo.com"}
        )
    finally:
        sender.send_email = original

    assert existe.status_code == nao_existe.status_code == 200
    assert existe.json() == nao_existe.json()


def test_a_failed_delivery_does_not_break_the_request(client, db):
    """Servidor de e-mail fora do ar continua devolvendo 200, não 500."""
    from app.services.email import sender

    user = _novo_usuario(db)
    original = sender.send_email

    def _explode(m):
        raise RuntimeError("smtp morreu de um jeito imprevisto")

    sender.send_email = _explode
    try:
        resp = client.post("/api/auth/forgot-password", json={"email": user.email})
    finally:
        sender.send_email = original

    assert resp.status_code == 200
```

> **Nota para quem executa:** confira os nomes das fixtures (`client`, `db`) e do helper (`_novo_usuario`) que já existem no arquivo e ajuste as chamadas acima para casar com eles. Se o helper tiver outro nome, use o que existe — não crie um duplicado.

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_password_reset_flow.py -v -k "email"`
Expected: FAIL — nenhum e-mail é enviado hoje, então `enviados` fica vazio.

- [ ] **Step 3: Substituir o corpo de `forgot_password` em `app/api/routes/auth.py`**

Troque a docstring e o corpo (da linha `"""` que abre a docstring até o `return`) por:

```python
    """
    Inicia o fluxo de recuperação de senha.

    Gera um token de redefinição de uso único e o envia POR E-MAIL ao dono do
    endereço. Responde sempre de forma genérica — mesma mensagem, mesmos campos,
    exista ou não o e-mail — para não revelar quais endereços estão cadastrados.

    O token nunca vai na resposta HTTP nem no log: quem faz o pedido não é
    necessariamente o dono do e-mail, e qualquer um desses dois canais entregaria
    a conta a quem soubesse o endereço.

    Falha de entrega NÃO muda a resposta. Um servidor de e-mail fora do ar não
    pode virar um oráculo de "esta conta existe" — nem por status, nem por corpo.
    """
    reset_token = auth_service.create_reset_token_for_email(db, email=payload.email)
    if reset_token is not None:
        user = auth_service.get_user_by_email(db, email=payload.email)
        reset_url = (
            f"{settings.APP_BASE_URL.rstrip('/')}/reset-password?token={reset_token}"
        )
        message = replace(
            render_password_reset(user.name if user else "", reset_url),
            to=payload.email,
        )
        try:
            send_email(message)
        except Exception:  # noqa: BLE001
            # `send_email` já promete não lançar; este except é a rede contra um
            # backend futuro que quebre a promessa. A resposta não pode mudar.
            logger.exception("Falha inesperada ao enviar o e-mail de redefinição.")

    return ForgotPasswordResponse(
        message=(
            "Se este e-mail estiver cadastrado, enviamos instruções para "
            "redefinir a senha."
        )
    )
```

- [ ] **Step 4: Acrescentar os imports em `app/api/routes/auth.py`**

```python
from dataclasses import replace

from app.core.config import settings
from app.services.email.messages import render_password_reset
from app.services.email.sender import send_email
```

- [ ] **Step 5: Expor `get_user_by_email` em `app/services/auth_service.py`**

A função privada `_get_user_by_email` já existe. Acrescente logo abaixo dela um alias público, porque a rota precisa do NOME do usuário para o corpo do e-mail:

```python
def get_user_by_email(db: Session, *, email: str) -> User | None:
    """
    Versão pública de `_get_user_by_email`, para quem precisa do usuário sem
    passar por autenticação — hoje, a rota de recuperação de senha, que monta o
    e-mail com o nome de quem vai recebê-lo.

    ⚠️ Quem chamar isto NÃO pode deixar o resultado influenciar a resposta HTTP:
    a diferença entre `None` e um usuário é exatamente a informação que o fluxo
    de recuperação existe para não vazar.
    """
    return _get_user_by_email(db, email)
```

- [ ] **Step 6: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_password_reset_flow.py -v`
Expected: PASS (todos, incluindo os 4 novos)

- [ ] **Step 7: Verificar de ponta a ponta com o Mailpit**

Run:
```bash
docker compose up -d
curl -s http://localhost:8025/api/v1/messages -X DELETE   # limpa a caixa
EMAIL_BACKEND=smtp .venv/bin/python -m uvicorn app.main:app --port 8001 &
sleep 4
curl -s -X POST http://localhost:8001/api/auth/forgot-password \
  -H 'content-type: application/json' -d '{"email":"teste@gmail.com"}'
sleep 1
curl -s http://localhost:8025/api/v1/messages | head -c 400
kill %1
```
Expected: a resposta genérica da API, e a caixa do Mailpit com uma mensagem de assunto "Redefinição de senha" para `teste@gmail.com`.

- [ ] **Step 8: Rodar a suíte inteira**

Run: `.venv/bin/python -m pytest -q`
Expected: 231 passed, 2 skipped.

- [ ] **Step 9: Commit**

```bash
git add app/api/routes/auth.py app/services/auth_service.py tests/test_password_reset_flow.py
git commit -m "feat(auth): token de redefinição vai por e-mail, não mais para o log"
```

---

### Task 4: Modelo e migration da verificação de e-mail

**Files:**
- Modify: `app/models/user.py`
- Create: `app/models/email_verification_token.py`
- Modify: `app/models/__init__.py`
- Create: `alembic/versions/0005_email_verification.py`
- Test: `tests/test_email_verification_model.py`

**Interfaces:**
- Consumes: nada de tarefas anteriores.
- Produces:
  - `User.email_verified_at: datetime | None`
  - `User.is_email_verified: bool` (propriedade)
  - `class EmailVerificationToken` com `user_id`, `token_hash`, `expires_at`, `used_at`
  - `User.email_verification_tokens` (relationship)

**Design:** o token de verificação copia deliberadamente o desenho do `PasswordResetToken` — valor opaco de 256 bits, só o SHA-256 persistido, uso único por `used_at`, revogação por emissão de um novo. As mesmas três propriedades são desejadas aqui, e ter dois desenhos diferentes para o mesmo problema no mesmo projeto seria pior que a duplicação.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_email_verification_model.py`:

```python
"""
Modelo da verificação de e-mail.

Roda contra o Postgres de DATABASE_URL (mesmo padrão de
tests/test_wardrobe_image_access.py). Sem banco acessível, os testes são PULADOS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import SessionLocal, engine
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User


@pytest.fixture(scope="module")
def db():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db) -> User:
    u = User(
        name="Quem Verifica",
        email=f"verif-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == u.id
    ).delete()
    db.delete(u)
    db.commit()


def test_a_new_account_starts_unverified(db, user):
    assert user.email_verified_at is None
    assert user.is_email_verified is False


def test_marking_the_timestamp_flips_the_property(db, user):
    user.email_verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    assert user.is_email_verified is True


def test_a_verification_token_stores_only_its_hash(db, user):
    """
    Mesmo desenho do token de reset: o valor em claro nunca é persistido, então
    um vazamento do banco não entrega tokens utilizáveis.
    """
    token = EmailVerificationToken(
        user_id=user.id,
        token_hash="a" * 64,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(token)
    db.commit()
    db.refresh(token)

    assert token.used_at is None
    assert len(token.token_hash) == 64
    assert not hasattr(token, "token")  # nada em claro no modelo


def test_tokens_die_with_their_user(db):
    """ON DELETE CASCADE: apagar a conta não pode deixar token órfão válido."""
    u = User(
        name="Efêmera",
        email=f"efemera-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    db.add(
        EmailVerificationToken(
            user_id=u.id,
            token_hash="b" * 64,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
    )
    db.commit()
    uid = u.id
    db.delete(u)
    db.commit()

    restantes = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.user_id == uid)
        .count()
    )
    assert restantes == 0
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_email_verification_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.email_verification_token'`

- [ ] **Step 3: Criar `app/models/email_verification_token.py`**

```python
"""
Modelo ORM da tabela `email_verification_tokens`.

Desenho copiado de propósito do `PasswordResetToken`: valor opaco e aleatório
(não um JWT), do qual só o SHA-256 é persistido. As três propriedades que
motivaram aquele desenho valem igual aqui:

  - uso único:  `used_at` é carimbado na primeira confirmação;
  - revogável:  emitir um token novo marca como usados os pendentes do usuário;
  - vazamento do banco não entrega tokens utilizáveis.

Ter dois desenhos diferentes para o mesmo problema no mesmo projeto seria pior
que a semelhança entre os dois arquivos.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class EmailVerificationToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 em hexadecimal (64 caracteres) do token entregue por e-mail.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Nulo enquanto pendente; carimbado ao ser consumido OU revogado.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="email_verification_tokens"
    )
```

- [ ] **Step 4: Acrescentar a coluna e a relação em `app/models/user.py`**

Depois de `token_version`, acrescente:

```python
    # Momento em que o dono do endereço confirmou que ele é dele. Nulo = não
    # verificado.
    #
    # É um TIMESTAMP e não um booleano de propósito: "quando" responde perguntas
    # que "se" não responde — há quanto tempo a conta está verificada, quantas
    # verificaram depois de tal mudança. Um booleano jogaria fora essa
    # informação para economizar 7 bytes.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

E na lista de relationships:

```python
    email_verification_tokens: Mapped[list["EmailVerificationToken"]] = relationship(  # noqa: F821
        back_populates="user", cascade="all, delete-orphan"
    )
```

E, ao final da classe, a propriedade de leitura:

```python
    @property
    def is_email_verified(self) -> bool:
        """Açúcar de leitura. A fonte da verdade é `email_verified_at`."""
        return self.email_verified_at is not None
```

> Confira os imports do arquivo: `datetime` e `DateTime` já podem estar presentes por causa de outras colunas. Não duplique.

- [ ] **Step 5: Registrar o modelo em `app/models/__init__.py`**

Acrescente o import na mesma forma dos existentes, para que o Alembic e o `Base.metadata` enxerguem a tabela:

```python
from app.models.email_verification_token import EmailVerificationToken  # noqa: F401
```

- [ ] **Step 6: Criar `alembic/versions/0005_email_verification.py`**

```python
"""Verificação de e-mail: coluna em users e tabela de tokens.

Revision ID: 0005_email_verification
Revises: 0004_user_token_version
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_email_verification"
down_revision = "0004_user_token_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nulo para todo mundo, inclusive as contas que já existem. Não há data de
    # verificação correta para retroagir, e inventar uma falsificaria o
    # histórico — o mesmo critério usado quando `ocasiao` entrou em looks_history.
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "email_verification_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"]
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_email_verification_tokens_token_hash", table_name="email_verification_tokens"
    )
    op.drop_index(
        "ix_email_verification_tokens_user_id", table_name="email_verification_tokens"
    )
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified_at")
```

> **Confira antes de escrever:** abra `alembic/versions/0004_user_token_version.py` e confirme o valor exato de `revision` que ele declara. `down_revision` acima precisa bater com esse valor literal, e o padrão de `server_default` para o id (`gen_random_uuid()` vs `uuid_generate_v4()`) precisa ser o mesmo usado em `0003_password_reset_tokens.py`. Copie do que existe.

- [ ] **Step 7: Aplicar a migration**

Run:
```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
```
Expected: `0005_email_verification (head)`.

- [ ] **Step 8: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_email_verification_model.py -v`
Expected: PASS (4 testes)

- [ ] **Step 9: Rodar a suíte inteira**

Run: `.venv/bin/python -m pytest -q`
Expected: 235 passed, 2 skipped.

- [ ] **Step 10: Commit**

```bash
git add app/models tests/test_email_verification_model.py alembic/versions/0005_email_verification.py
git commit -m "feat(auth): modelo e migration da verificação de e-mail"
```

---
### Task 5: Fluxo de verificação de e-mail

**Files:**
- Modify: `app/core/config.py` (duas variáveis novas)
- Modify: `app/core/security.py` (dois helpers)
- Modify: `app/services/auth_service.py`
- Modify: `app/api/routes/auth.py`
- Modify: `app/schemas/auth.py`, `app/schemas/user.py`
- Test: `tests/test_email_verification_flow.py`

**Interfaces:**
- Consumes: `EmailVerificationToken`, `User.email_verified_at` (Task 4); `send_email`, `render_email_verification` (Task 2).
- Produces:
  - `generate_verification_token() -> str`, `hash_verification_token(t) -> str`
  - `auth_service.issue_email_verification(db, *, user) -> str | None`
  - `auth_service.confirm_email_verification(db, *, token) -> bool`
  - `POST /api/auth/verify-email` `{token}` → `{message}`
  - `POST /api/auth/resend-verification` `{email}` → `{message}` (genérico)
  - `UserPublic.email_verified: bool`
  - `settings.REQUIRE_VERIFIED_EMAIL: bool = False`, `EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 24`

**DECISÃO DE PRODUTO, já tomada (spec D5): o login NÃO é bloqueado por padrão.**
`REQUIRE_VERIFIED_EMAIL` existe e vem `false`. Motivo: uma trava que depende de um contêiner de e-mail rodando é uma trava que prende o próprio dono do projeto, e os dois usuários já no banco não têm como ter verificado nada. O estado é exposto em `/api/auth/me` para o frontend sinalizar. Ligue a flag quando houver entrega de e-mail confiável.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_email_verification_flow.py`:

```python
"""
Fluxo de verificação de e-mail, pela borda HTTP.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.

O que estes testes protegem, em ordem de importância:
  1. o token não vaza pela resposta HTTP (só pelo e-mail);
  2. o reenvio responde igual exista ou não a conta (senão vira enumeração);
  3. token usado, expirado ou inventado é recusado do mesmo jeito;
  4. a flag de bloqueio de login funciona nos dois estados.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User
from app.services.email import sender


@pytest.fixture(scope="module")
def client():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def caixa(monkeypatch):
    """Substitui o envio real e devolve a lista do que foi 'enviado'."""
    enviados = []
    monkeypatch.setattr(sender, "send_email", lambda m: enviados.append(m) or True)
    return enviados


def _limpa(db, email):
    u = db.query(User).filter(User.email == email).one_or_none()
    if u:
        db.query(EmailVerificationToken).filter(
            EmailVerificationToken.user_id == u.id
        ).delete()
        db.delete(u)
        db.commit()


def _token_do_email(texto: str) -> str:
    import re

    achado = re.search(r"token=([A-Za-z0-9_\-]{20,})", texto)
    assert achado, f"nenhum token no corpo do e-mail: {texto!r}"
    return achado.group(1)


@pytest.fixture
def cadastrado(client, db, caixa):
    email = f"verif-{uuid.uuid4().hex}@exemplo.com"
    resp = client.post(
        "/api/auth/register",
        json={"name": "Quem Verifica", "email": email, "password": "Uma-Senha-Muito-Longa-9"},
    )
    assert resp.status_code in (200, 201), resp.text
    yield email
    _limpa(db, email)


# ── Envio no cadastro ───────────────────────────────────────────────────────
def test_registering_sends_a_verification_email(cadastrado, caixa):
    assert len(caixa) == 1
    assert caixa[0].to == cadastrado
    _token_do_email(caixa[0].text)


def test_the_token_never_appears_in_the_http_response(client, db, caixa):
    email = f"verif-{uuid.uuid4().hex}@exemplo.com"
    resp = client.post(
        "/api/auth/register",
        json={"name": "X", "email": email, "password": "Uma-Senha-Muito-Longa-9"},
    )
    try:
        token = _token_do_email(caixa[0].text)
        assert token not in resp.text
    finally:
        _limpa(db, email)


def test_a_new_account_reports_itself_unverified(client, cadastrado, caixa):
    login = client.post(
        "/api/auth/login",
        json={"email": cadastrado, "password": "Uma-Senha-Muito-Longa-9"},
    )
    assert login.status_code == 200
    me = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert me.json()["email_verified"] is False


# ── Confirmação ─────────────────────────────────────────────────────────────
def test_a_valid_token_verifies_the_account(client, db, cadastrado, caixa):
    token = _token_do_email(caixa[0].text)
    resp = client.post("/api/auth/verify-email", json={"token": token})
    assert resp.status_code == 200

    user = db.query(User).filter(User.email == cadastrado).one()
    db.refresh(user)
    assert user.is_email_verified is True


def test_a_token_cannot_be_used_twice(client, cadastrado, caixa):
    token = _token_do_email(caixa[0].text)
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
    segunda = client.post("/api/auth/verify-email", json={"token": token})
    assert segunda.status_code == 400


def test_an_invented_token_is_refused(client):
    resp = client.post("/api/auth/verify-email", json={"token": "nao-existe-" + "x" * 30})
    assert resp.status_code == 400


def test_an_expired_token_is_refused(client, db, cadastrado, caixa):
    token = _token_do_email(caixa[0].text)
    user = db.query(User).filter(User.email == cadastrado).one()
    row = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.user_id == user.id)
        .one()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400


def test_refusal_messages_are_identical_for_invalid_and_expired(client, db, cadastrado, caixa):
    """
    Distinguir "inválido" de "expirado" diria a um atacante que ele acertou um
    token que existiu — informação que não tem por que sair daqui.
    """
    inventado = client.post("/api/auth/verify-email", json={"token": "z" * 40})

    token = _token_do_email(caixa[0].text)
    user = db.query(User).filter(User.email == cadastrado).one()
    row = (
        db.query(EmailVerificationToken)
        .filter(EmailVerificationToken.user_id == user.id)
        .one()
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    expirado = client.post("/api/auth/verify-email", json={"token": token})

    assert inventado.status_code == expirado.status_code
    assert inventado.json() == expirado.json()


# ── Reenvio ─────────────────────────────────────────────────────────────────
def test_resend_sends_a_new_token_for_a_real_pending_account(client, cadastrado, caixa):
    caixa.clear()
    resp = client.post("/api/auth/resend-verification", json={"email": cadastrado})
    assert resp.status_code == 200
    assert len(caixa) == 1


def test_resend_answers_the_same_for_an_unknown_address(client, cadastrado, caixa):
    conhecido = client.post("/api/auth/resend-verification", json={"email": cadastrado})
    caixa.clear()
    desconhecido = client.post(
        "/api/auth/resend-verification", json={"email": "ninguem@exemplo.com"}
    )
    assert conhecido.status_code == desconhecido.status_code == 200
    assert conhecido.json() == desconhecido.json()
    assert caixa == [], "não há conta: não há para quem enviar"


def test_resend_revokes_the_previous_token(client, cadastrado, caixa):
    primeiro = _token_do_email(caixa[0].text)
    caixa.clear()
    client.post("/api/auth/resend-verification", json={"email": cadastrado})
    segundo = _token_do_email(caixa[0].text)
    assert primeiro != segundo

    # O antigo morreu; só o novo vale.
    assert client.post("/api/auth/verify-email", json={"token": primeiro}).status_code == 400
    assert client.post("/api/auth/verify-email", json={"token": segundo}).status_code == 200


# ── A flag de bloqueio ──────────────────────────────────────────────────────
def test_login_works_unverified_by_default(client, cadastrado, caixa):
    assert settings.REQUIRE_VERIFIED_EMAIL is False
    resp = client.post(
        "/api/auth/login",
        json={"email": cadastrado, "password": "Uma-Senha-Muito-Longa-9"},
    )
    assert resp.status_code == 200


def test_login_is_blocked_when_the_flag_is_on(client, cadastrado, caixa, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_VERIFIED_EMAIL", True)
    resp = client.post(
        "/api/auth/login",
        json={"email": cadastrado, "password": "Uma-Senha-Muito-Longa-9"},
    )
    assert resp.status_code == 403


def test_the_block_does_not_leak_which_accounts_exist(client, cadastrado, caixa, monkeypatch):
    """
    Bloquear por não-verificado só pode acontecer DEPOIS de a senha conferir.
    Se a rota respondesse 403 antes disso, bastaria tentar um e-mail qualquer
    para descobrir se ele tem conta.
    """
    monkeypatch.setattr(settings, "REQUIRE_VERIFIED_EMAIL", True)
    senha_errada = client.post(
        "/api/auth/login", json={"email": cadastrado, "password": "senha-errada-aqui-1"}
    )
    inexistente = client.post(
        "/api/auth/login",
        json={"email": "ninguem@exemplo.com", "password": "senha-errada-aqui-1"},
    )
    assert senha_errada.status_code == inexistente.status_code == 401
    assert senha_errada.json() == inexistente.json()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_email_verification_flow.py -v`
Expected: FAIL — a rota `/api/auth/verify-email` ainda não existe (404).

- [ ] **Step 3: Configuração em `app/core/config.py`**

Depois do bloco de e-mail da Task 2:

```python
    # Exigir e-mail verificado para entrar.
    #
    # DESLIGADO por padrão, e isso é deliberado. Ligar isto torna a aplicação
    # inutilizável se o servidor de e-mail não estiver de pé — uma trava que
    # depende de um contêiner rodando é uma trava que prende o dono do projeto.
    # Além disso, as contas criadas ANTES desta feature não têm como ter
    # verificado nada e ficariam trancadas para fora.
    #
    # Ligue quando houver entrega de e-mail confiável (EMAIL_BACKEND=resend com
    # domínio verificado) e depois de verificar as contas que já existem.
    REQUIRE_VERIFIED_EMAIL: bool = False

    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 24
```

- [ ] **Step 4: Helpers em `app/core/security.py`**

Acrescente ao lado dos helpers de reset (que usam o mesmo desenho):

```python
# O token de verificação usa exatamente o mesmo tamanho e a mesma função de
# hash do de reset. São o mesmo problema — segredo opaco de uso único cujo
# valor em claro não pode ser persistido — e resolvê-los diferente só criaria
# duas superfícies para auditar.
def generate_verification_token() -> str:
    return secrets.token_urlsafe(_RESET_TOKEN_BYTES)


def hash_verification_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
```

- [ ] **Step 5: Serviço em `app/services/auth_service.py`**

```python
def issue_email_verification(db: Session, *, user: User) -> str | None:
    """
    Emite um token de verificação e revoga os pendentes do mesmo usuário.

    Returns:
        O token EM CLARO, para ir no e-mail. `None` se a conta já está
        verificada — nesse caso não há nada a confirmar e emitir um token novo
        só criaria um segredo válido sem propósito.
    """
    if user.is_email_verified:
        return None

    # Só o último pedido vale: um token novo invalida os anteriores, do mesmo
    # jeito que no reset de senha.
    db.query(EmailVerificationToken).filter(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used_at.is_(None),
    ).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)

    raw = generate_verification_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_verification_token(raw),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS),
        )
    )
    db.commit()
    return raw


def confirm_email_verification(db: Session, *, token: str) -> bool:
    """
    Consome um token e marca a conta como verificada.

    Returns:
        True se confirmou. False para token inexistente, já usado ou expirado —
        os três casos devolvem a MESMA coisa de propósito: distinguir "expirado"
        de "inválido" diria ao atacante que ele acertou um token que existiu.
    """
    row = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == hash_verification_token(token)
        )
    )
    now = datetime.now(timezone.utc)
    if row is None or row.used_at is not None or row.expires_at <= now:
        return False

    row.used_at = now
    user = db.get(User, row.user_id)
    if user is None:
        return False
    user.email_verified_at = now
    db.commit()
    return True


def send_verification_email(db: Session, *, user: User) -> None:
    """
    Emite o token e despacha o e-mail. Nunca lança.

    Falha de entrega não pode derrubar o cadastro nem mudar a resposta HTTP: a
    conta foi criada de qualquer jeito, e o usuário tem a rota de reenvio.
    """
    raw = issue_email_verification(db, user=user)
    if raw is None:
        return
    url = f"{settings.APP_BASE_URL.rstrip('/')}/verificar-email?token={raw}"
    try:
        send_email(replace(render_email_verification(user.name, url), to=user.email))
    except Exception:  # noqa: BLE001
        logger.exception("Falha inesperada ao enviar o e-mail de verificação.")
```

> Acrescente os imports necessários no topo do arquivo: `timedelta`, `timezone`, `replace` (de `dataclasses`), `EmailVerificationToken`, `generate_verification_token`, `hash_verification_token`, `settings`, `send_email`, `render_email_verification`, e um `logger = logging.getLogger("miranda.auth")` se ainda não houver.

- [ ] **Step 6: Bloqueio opcional no login**

Em `authenticate_user`, DEPOIS da verificação de senha (nunca antes — ver o teste `test_the_block_does_not_leak_which_accounts_exist`):

```python
    # A checagem vem DEPOIS de a senha conferir, de propósito. Se um e-mail não
    # verificado respondesse 403 antes disso, bastaria tentar qualquer endereço
    # com senha inventada para descobrir quais têm conta — exatamente a
    # enumeração que o resto deste módulo trabalha para impedir.
    if settings.REQUIRE_VERIFIED_EMAIL and not user.is_email_verified:
        raise AuthError(403, "Confirme seu e-mail antes de entrar.")
```

- [ ] **Step 7: Schemas**

Em `app/schemas/auth.py`:

```python
class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class GenericMessageResponse(BaseModel):
    message: str
```

Em `app/schemas/user.py`, acrescente ao `UserPublic`:

```python
    # Exposto para o frontend poder sinalizar o estado. Não bloqueia nada por
    # padrão — ver settings.REQUIRE_VERIFIED_EMAIL.
    email_verified: bool = False
```

E, para que o campo saia preenchido a partir do ORM, acrescente o validador:

```python
    @field_validator("email_verified", mode="before")
    @classmethod
    def _from_timestamp(cls, v, info):
        return v
```

> **Nota para quem executa:** o caminho mais simples e menos frágil é dar ao ORM a propriedade `is_email_verified` (Task 4) e, no `UserPublic`, usar `email_verified: bool` com `model_config = ConfigDict(from_attributes=True)` mais um `@computed_field` ou montar o schema explicitamente na rota `/me`. Escolha UM e faça só ele — não deixe dois caminhos. Se `from_attributes` não casar o nome, monte explicitamente na rota: `UserPublic(..., email_verified=current_user.is_email_verified)`.

- [ ] **Step 8: Rotas em `app/api/routes/auth.py`**

```python
@router.post(
    "/verify-email",
    response_model=GenericMessageResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def verify_email(
    request: Request,
    response: Response,
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> GenericMessageResponse:
    """
    Confirma o endereço a partir do token recebido por e-mail.

    Sob rate limit porque o token é adivinhável por força bruta em tese — 256
    bits tornam isso irrealista, mas o teto custa nada e fecha a porta.
    """
    if not auth_service.confirm_email_verification(db, token=payload.token):
        raise HTTPException(400, "Link de confirmação inválido ou expirado.")
    return GenericMessageResponse(message="E-mail confirmado.")


@router.post(
    "/resend-verification",
    response_model=GenericMessageResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def resend_verification(
    request: Request,
    response: Response,
    payload: ResendVerificationRequest,
    db: Session = Depends(get_db),
) -> GenericMessageResponse:
    """
    Reenvia o e-mail de confirmação.

    Resposta genérica sempre: exista ou não a conta, esteja ou não verificada, o
    corpo é o mesmo. Variar aqui transformaria esta rota num verificador de
    e-mails cadastrados — a mesma falha que `forgot-password` já evita.
    """
    user = auth_service.get_user_by_email(db, email=payload.email)
    if user is not None:
        auth_service.send_verification_email(db, user=user)
    return GenericMessageResponse(
        message=(
            "Se este e-mail estiver cadastrado e ainda não confirmado, "
            "enviamos um novo link."
        )
    )
```

- [ ] **Step 9: Disparar no cadastro**

Na rota `register`, depois de `auth_service.register_user(...)` e antes do `return`:

```python
    auth_service.send_verification_email(db, user=user)
```

- [ ] **Step 10: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_email_verification_flow.py -v`
Expected: PASS (14 testes)

- [ ] **Step 11: Suíte inteira + commit**

Run: `.venv/bin/python -m pytest -q` → 249 passed, 2 skipped.

```bash
git add app/core app/services/auth_service.py app/api/routes/auth.py app/schemas tests/test_email_verification_flow.py
git commit -m "feat(auth): fluxo de verificação de e-mail, sem bloquear login por padrão"
```

---

### Task 6: Cadastro genérico e aviso ao dono (fecha o item #4 da revisão de segurança)

Hoje o cadastro responde `409 "Este e-mail já está cadastrado."`, o que confirma a existência de uma conta em uma requisição.

**Files:**
- Modify: `app/services/auth_service.py` (`register_user`)
- Modify: `app/api/routes/auth.py` (rota `register`)
- Test: `tests/test_signup_enumeration.py`

**Interfaces:**
- Consumes: `render_duplicate_signup_notice` (Task 2), `send_verification_email` (Task 5).
- Produces: `auth_service.register_or_notify(db, *, name, email, password) -> User | None` — `None` quando o e-mail já existia.

**Design:** a rota passa a responder **sempre** `201` com o mesmo corpo. Quando o e-mail já existe, nenhuma conta é criada e o dono do endereço recebe um aviso — o único canal que só ele lê. O custo é de experiência: quem esqueceu que já tinha conta não vê mais o erro na tela. É o preço de fechar a enumeração, e está documentado no README.

⚠️ **O tempo de resposta também precisa ser igual.** O caminho "e-mail novo" roda `hash_password` (bcrypt, ~250 ms) e o caminho "já existe" não rodaria — a diferença é mensurável e reabriria a enumeração pelo relógio, exatamente como já aconteceu no login (ver README 16.2). A solução é a mesma de lá: rodar o bcrypt sempre.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_signup_enumeration.py`:

```python
"""
Enumeração de contas no cadastro (item #4 da revisão de segurança).

O login já não vaza — nem por mensagem, nem por tempo. O cadastro vazava, e
por dois canais: a mensagem 409 e o custo do bcrypt, que só era pago no caminho
do e-mail novo.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.main import app
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User
from app.services.email import sender

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
    monkeypatch.setattr(sender, "send_email", lambda m: enviados.append(m) or True)
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
    client.post("/api/auth/register", json={"name": "Dono", "email": email, "password": SENHA})
    caixa.clear()
    yield email
    _limpa(db, email)


def _cadastrar(client, email):
    return client.post(
        "/api/auth/register", json={"name": "Alguém", "email": email, "password": SENHA}
    )


# ── O canal da mensagem ─────────────────────────────────────────────────────
def test_signing_up_with_an_existing_email_looks_identical(client, db, existente, caixa):
    novo = f"novo-{uuid.uuid4().hex}@exemplo.com"
    try:
        repetido = _cadastrar(client, existente)
        inedito = _cadastrar(client, novo)
        assert repetido.status_code == inedito.status_code
        assert repetido.json() == inedito.json()
    finally:
        _limpa(db, novo)


def test_no_second_account_is_created(client, db, existente, caixa):
    _cadastrar(client, existente)
    assert db.query(User).filter(User.email == existente).count() == 1


def test_the_owner_is_warned_by_email(client, existente, caixa):
    _cadastrar(client, existente)
    assert len(caixa) == 1
    assert caixa[0].to == existente
    assert "tentou criar uma conta" in caixa[0].text.lower()


def test_the_warning_carries_nothing_actionable(client, existente, caixa):
    """Quem disparou a tentativa pode não ser o dono: nada de token ou link."""
    _cadastrar(client, existente)
    texto = caixa[0].text
    assert "token=" not in texto
    assert SENHA not in texto


def test_a_genuinely_new_signup_still_gets_its_verification_email(client, db, caixa):
    novo = f"novo-{uuid.uuid4().hex}@exemplo.com"
    try:
        _cadastrar(client, novo)
        assert len(caixa) == 1
        assert "confirm" in caixa[0].subject.lower()
    finally:
        _limpa(db, novo)


# ── O canal do relógio ──────────────────────────────────────────────────────
def test_both_paths_cost_about_the_same_time(client, db, existente, caixa):
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
        inicio = time.perf_counter()
        _cadastrar(client, email)
        return time.perf_counter() - inicio

    def _mede_repetido():
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
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_signup_enumeration.py -v`
Expected: FAIL — a primeira asserção quebra (409 contra 201).

- [ ] **Step 3: Substituir `register_user` em `app/services/auth_service.py`**

```python
def register_or_notify(
    db: Session, *, name: str, email: str, password: str
) -> User | None:
    """
    Cria a conta OU, se o e-mail já existir, avisa o dono e não cria nada.

    Returns:
        O usuário criado, ou `None` quando o e-mail já estava cadastrado.

    ⚠️ Quem chama NÃO pode deixar esse `None` mudar a resposta HTTP. Ele existe
    para a rota saber se deve disparar o e-mail de verificação, não para virar
    um código de status diferente — a diferença entre os dois casos é
    exatamente o que o item #4 da revisão de segurança manda esconder.

    O `hash_password` roda nos DOIS caminhos, mesmo quando o hash é jogado fora.
    Sem isso, o caminho do e-mail repetido responderia sem pagar os ~250 ms do
    bcrypt, e o relógio entregaria quais endereços têm conta — a mesma falha que
    `authenticate_user` corrige com o `DUMMY_PASSWORD_HASH`.
    """
    email = email.lower()
    hashed = hash_password(password)

    existente = _get_user_by_email(db, email)
    if existente is not None:
        try:
            send_email(
                replace(
                    render_duplicate_signup_notice(existente.name), to=existente.email
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao avisar o dono da conta sobre cadastro repetido.")
        return None

    user = User(name=name.strip(), email=email, hashed_password=hashed)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
```

> Mantenha `register_user` como está SE algum outro chamador depender dela; caso contrário, remova-a. Rode `grep -rn "register_user" app tests scripts` antes de decidir e não deixe uma função morta para trás.

- [ ] **Step 4: Ajustar a rota `register`**

```python
    user = auth_service.register_or_notify(
        db, name=payload.name, email=payload.email, password=payload.password
    )
    if user is not None:
        auth_service.send_verification_email(db, user=user)

    # Mesma resposta nos dois casos. Não devolvemos o usuário criado: o corpo
    # precisa ser idêntico exista ou não a conta, e um objeto de usuário só
    # existiria em um dos caminhos.
    return GenericMessageResponse(
        message=(
            "Cadastro recebido. Se este e-mail ainda não tiver conta, "
            "enviamos um link de confirmação."
        )
    )
```

Ajuste `response_model=GenericMessageResponse` e mantenha `status_code=201`.

> **Atenção ao frontend:** a tela de cadastro (`miranda/app/register/page.tsx`) hoje espera receber um usuário e/ou tratar o 409. Ela precisa passar a exibir a mensagem genérica. Isso é ajustado na Task 9, junto com a migração para cookie — anote e não deixe o frontend quebrado entre as duas tarefas: se a Task 9 ainda não rodou, confira manualmente que a tela não quebra com a resposta nova.

- [ ] **Step 5: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_signup_enumeration.py -v`
Expected: PASS (6 testes). O teste de tempo é o mais lento (~10 cadastros com bcrypt).

- [ ] **Step 6: Suíte inteira + commit**

Run: `.venv/bin/python -m pytest -q` → 255 passed, 2 skipped.

```bash
git add app/services/auth_service.py app/api/routes/auth.py tests/test_signup_enumeration.py
git commit -m "fix(auth): fecha a enumeração de contas no cadastro (item #4)"
```

---
### Task 7: Rate limiter no Redis

**Files:**
- Modify: `app/core/config.py`, `app/core/rate_limit.py`, `.env.example`
- Test: `tests/test_rate_limit_storage.py`

**Interfaces:**
- Consumes: Redis em `localhost:6379` (Task 1).
- Produces: `settings.RATE_LIMIT_STORAGE_URI: str`.

**O problema que isto resolve:** com `storage_uri="memory://"`, cada worker do uvicorn tem a própria cota. Com 4 workers, o teto de 5 tentativas por 15 min vira 20 na prática — o rate limit de autenticação passa a valer um quarto do que aparenta.

**Degradação:** Redis fora do ar não pode derrubar a API. O slowapi cai para memória com um aviso no log — pior que Redis, melhor que 500 em toda rota de auth.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_rate_limit_storage.py`:

```python
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


def test_the_default_points_at_local_redis():
    assert _settings().RATE_LIMIT_STORAGE_URI == "redis://localhost:6379/0"


def test_the_uri_is_configurable():
    s = _settings(RATE_LIMIT_STORAGE_URI="redis://outro-host:6379/3")
    assert s.RATE_LIMIT_STORAGE_URI == "redis://outro-host:6379/3"


def test_memory_is_still_accepted_for_a_single_worker_machine():
    s = _settings(RATE_LIMIT_STORAGE_URI="memory://")
    assert s.RATE_LIMIT_STORAGE_URI == "memory://"


def test_an_unreachable_redis_falls_back_to_memory_instead_of_crashing(caplog):
    """
    Redis fora do ar degrada o rate limit, não a aplicação. Um teto por worker
    é pior que um teto global — mas é infinitamente melhor que HTTP 500 em toda
    rota de autenticação.
    """
    with caplog.at_level(logging.WARNING, logger="miranda.rate_limit"):
        uri = rate_limit.resolve_storage_uri("redis://127.0.0.1:1/0")
    assert uri == "memory://"
    assert "memory" in caplog.text.lower()


def test_a_reachable_redis_is_used_as_is():
    """Exige o Redis do docker compose; sem ele, o teste é pulado."""
    import redis

    try:
        redis.Redis.from_url("redis://localhost:6379/0", socket_connect_timeout=1).ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis indisponível — teste pulado ({type(exc).__name__}).")

    assert rate_limit.resolve_storage_uri("redis://localhost:6379/0") == (
        "redis://localhost:6379/0"
    )
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_rate_limit_storage.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'RATE_LIMIT_STORAGE_URI'`

- [ ] **Step 3: Dependência**

Acrescente ao `requirements.txt`, na seção de rate limiting:

```
# Storage do rate limiter fora do processo. Em memória, cada worker do uvicorn
# tem a própria cota — com 4 workers, um teto de 5 vira 20 na prática.
redis==5.2.1
```

Run: `.venv/bin/python -m pip install redis==5.2.1`

- [ ] **Step 4: Configuração**

Em `app/core/config.py`:

```python
    # ── Rate limiting ─────────────────────────────────────────────────
    # Onde o slowapi guarda os contadores. `memory://` só é correto com UM
    # worker: com vários, cada processo tem a própria cota e o teto configurado
    # vale N vezes mais do que aparenta.
    #
    # Redis indisponível NÃO derruba a API — cai para memória com aviso no log
    # (ver `rate_limit.resolve_storage_uri`).
    RATE_LIMIT_STORAGE_URI: str = "redis://localhost:6379/0"
```

- [ ] **Step 5: Resolver o storage em `app/core/rate_limit.py`**

Acrescente antes da criação do `limiter` e troque o `storage_uri` fixo:

```python
import logging

logger = logging.getLogger("miranda.rate_limit")


def resolve_storage_uri(uri: str | None = None) -> str:
    """
    Devolve a URI de storage a usar, confirmando que o Redis responde.

    A confirmação acontece UMA vez, no import. Sem ela, um Redis fora do ar só
    apareceria na primeira requisição de auth — como erro, não como aviso — e o
    modo de falha seria HTTP 500 numa rota de login em vez de um limite mais
    frouxo.
    """
    uri = uri if uri is not None else settings.RATE_LIMIT_STORAGE_URI
    if not uri.startswith("redis://") and not uri.startswith("rediss://"):
        return uri

    try:
        import redis

        redis.Redis.from_url(uri, socket_connect_timeout=1).ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Redis inacessível em %s (%s). O rate limit cai para memory:// — "
            "correto com UM worker apenas. Suba o Redis com `docker compose up -d`.",
            uri,
            exc,
        )
        return "memory://"
    return uri


limiter = Limiter(
    # ... mantenha os demais argumentos como estão ...
    storage_uri=resolve_storage_uri(),
)
```

> Preserve todos os outros argumentos do `Limiter` existente (key_func, default_limits, etc.). Só o `storage_uri` muda.

- [ ] **Step 6: `.env.example`**

```
# Storage do rate limiter. `memory://` só é correto com UM worker do uvicorn.
# Suba o Redis com `docker compose up -d` (ver README, seção de instalação).
RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0
```

- [ ] **Step 7: Verificar os dois caminhos na prática**

Run:
```bash
docker compose up -d redis
.venv/bin/python -c "from app.core.rate_limit import resolve_storage_uri as r; print('com redis:', r())"
docker compose stop redis
.venv/bin/python -c "from app.core.rate_limit import resolve_storage_uri as r; print('sem redis:', r())"
docker compose up -d redis
```
Expected: `com redis: redis://localhost:6379/0` e `sem redis: memory://` com o aviso no log.

- [ ] **Step 8: Confirmar que o rate limit ainda funciona ponta a ponta**

Run: `.venv/bin/python -m pytest tests/test_auth_rate_limit.py -v`
Expected: PASS — o comportamento não muda, só onde os contadores vivem.

- [ ] **Step 9: Suíte inteira + commit**

Run: `.venv/bin/python -m pytest -q` → 260 passed, 2 skipped.

```bash
git add app/core/config.py app/core/rate_limit.py requirements.txt .env.example tests/test_rate_limit_storage.py
git commit -m "feat(seguranca): rate limiter no Redis, com queda para memória"
```

---

### Task 8: Cookie httpOnly — backend

**Files:**
- Modify: `app/core/config.py`, `app/core/security.py` (nada), `app/api/deps.py`, `app/api/routes/auth.py`, `app/main.py` (CORS)
- Test: `tests/test_cookie_auth.py`

**Interfaces:**
- Consumes: nada de tarefas anteriores.
- Produces:
  - `settings.AUTH_COOKIE_NAME: str = "miranda_session"`, `AUTH_COOKIE_SECURE: bool = False`, `AUTH_COOKIE_SAMESITE: str = "lax"`
  - `POST /api/auth/login` passa a **setar cookie** além de devolver o corpo atual
  - `POST /api/auth/logout` → limpa o cookie
  - `get_current_user` aceita **cookie OU header** `Authorization`

**⚠️ CSRF — a contrapartida obrigatória desta troca.** O header `Authorization` tinha uma propriedade que o cookie não tem: o navegador não o envia sozinho. Com cookie, qualquer site pode disparar uma requisição autenticada para a API. As três defesas, todas necessárias:

1. `SameSite=Lax` — o navegador não envia o cookie em requisição cross-site que não seja navegação de topo. Cobre `POST`/`PUT`/`DELETE` disparados por outro site, que é o essencial aqui.
2. CORS restrito às origens de `CORS_ORIGINS` — já configurado, e o guard que proíbe `*` com credenciais já existe (`_reject_cors_wildcard_with_credentials`).
3. O header continua aceito, para que clientes não-navegador não precisem de cookie.

`SameSite=Strict` seria mais forte, mas quebraria a volta do link de verificação de e-mail (navegação vinda do cliente de e-mail). `Lax` é a escolha, e o motivo fica no código.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_cookie_auth.py`:

```python
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
from app.main import app
from app.models.user import User
from app.services.email import sender

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


@pytest.fixture
def conta(client, monkeypatch):
    monkeypatch.setattr(sender, "send_email", lambda m: True)
    email = f"cookie-{uuid.uuid4().hex}@exemplo.com"
    client.post(
        "/api/auth/register", json={"name": "Cookie", "email": email, "password": SENHA}
    )
    yield email
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
def test_login_sets_an_httponly_cookie(client, conta):
    resp = _login(client, conta)
    assert resp.status_code == 200
    cookie = resp.cookies.get(settings.AUTH_COOKIE_NAME)
    assert cookie, "o login precisa setar o cookie de sessão"

    bruto = resp.headers["set-cookie"].lower()
    assert "httponly" in bruto, "sem HttpOnly, o XSS continua alcançando o token"
    assert "samesite=lax" in bruto, "sem SameSite, o cookie abre CSRF"
    assert "path=/" in bruto


def test_the_cookie_alone_authenticates(client, conta):
    _login(client, conta)  # o TestClient guarda o cookie
    resp = client.get("/api/auth/me")  # sem header Authorization
    assert resp.status_code == 200
    assert resp.json()["email"] == conta


def test_the_authorization_header_still_works(client, conta):
    """
    Clientes não-navegador (scripts, o app mobile de amanhã) não têm por que
    lidar com cookie. O header continua aceito.
    """
    token = _login(client, conta).json()["access_token"]
    limpo = TestClient(app)
    resp = limpo.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_logout_clears_the_cookie(client, conta):
    _login(client, conta)
    saida = client.post("/api/auth/logout")
    assert saida.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_no_cookie_and_no_header_is_401(client):
    limpo = TestClient(app)
    assert limpo.get("/api/auth/me").status_code == 401


def test_a_garbage_cookie_is_401_not_500(client):
    limpo = TestClient(app)
    limpo.cookies.set(settings.AUTH_COOKIE_NAME, "isto-nao-e-um-jwt")
    assert limpo.get("/api/auth/me").status_code == 401


def test_the_header_wins_when_both_are_present(client, conta):
    """
    Regra explícita para não ficar indefinida: o header é o canal explícito de
    quem sabe o que está fazendo, então ele manda.
    """
    _login(client, conta)
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer lixo"})
    assert resp.status_code == 401, "o header inválido não pode ser ignorado em favor do cookie"


# ── Defesa contra CSRF ──────────────────────────────────────────────────────
def test_cors_does_not_allow_an_unknown_origin_with_credentials(client, conta):
    """
    Com cookie automático, CORS deixa de ser cosmético: é o que impede outro
    site de ler a resposta de uma requisição autenticada.
    """
    _login(client, conta)
    resp = client.get("/api/auth/me", headers={"Origin": "https://site-do-atacante.example"})
    assert resp.headers.get("access-control-allow-origin") != "https://site-do-atacante.example"


def test_the_secure_flag_follows_configuration(client, conta, monkeypatch):
    """
    Localmente o cookie não pode ser `Secure` — a API roda em http e o navegador
    descartaria. Em produção precisa ser, e é o que a flag controla.
    """
    monkeypatch.setattr(settings, "AUTH_COOKIE_SECURE", True)
    limpo = TestClient(app)
    resp = limpo.post("/api/auth/login", json={"email": conta, "password": SENHA})
    assert "secure" in resp.headers["set-cookie"].lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_cookie_auth.py -v`
Expected: FAIL — `AttributeError: ... AUTH_COOKIE_NAME`

- [ ] **Step 3: Configuração**

```python
    # ── Cookie de sessão ──────────────────────────────────────────────
    # O JWT saiu do localStorage (alcançável por qualquer XSS) e passou a
    # viajar num cookie httpOnly, que o JavaScript não lê.
    #
    # A contrapartida é CSRF: o navegador passa a mandar a credencial sozinho.
    # SameSite=Lax é a defesa principal — o cookie não vai em requisição
    # cross-site que não seja navegação de topo.
    #
    # `Strict` seria mais forte e QUEBRARIA a volta do link de verificação de
    # e-mail, que é exatamente uma navegação de topo vinda de outro contexto.
    AUTH_COOKIE_NAME: str = "miranda_session"
    AUTH_COOKIE_SAMESITE: str = "lax"
    # `Secure` faz o navegador só enviar o cookie por HTTPS. DESLIGADO por
    # padrão porque em desenvolvimento a API roda em http://localhost e o
    # navegador descartaria o cookie. LIGUE EM PRODUÇÃO — ver checklist de
    # deploy no README.
    AUTH_COOKIE_SECURE: bool = False
```

- [ ] **Step 4: Aceitar cookie em `app/api/deps.py`**

```python
def _extract_token(request: Request) -> str | None:
    """
    Pega o token do header `Authorization` ou do cookie de sessão, nessa ordem.

    O header ganha de propósito: é o canal explícito de quem sabe o que está
    fazendo (scripts, testes, um app nativo). Se ele veio e está errado, a
    requisição falha — cair para o cookie ali deixaria um header inválido ser
    silenciosamente ignorado, que é o tipo de comportamento que esconde bug.
    """
    header = request.headers.get("Authorization")
    if header:
        scheme, _, value = header.partition(" ")
        return value if scheme.lower() == "bearer" else None
    return request.cookies.get(settings.AUTH_COOKIE_NAME)
```

Reescreva `get_current_user` para usar `_extract_token(request)` em vez do `HTTPBearer`, mantendo o mesmo 401 e a mesma mensagem de hoje.

- [ ] **Step 5: Setar e limpar o cookie nas rotas**

Em `login`, depois de obter o token:

```python
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=settings.AUTH_COOKIE_SECURE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
```

O corpo continua devolvendo `access_token`: clientes não-navegador dependem dele, e há teste para isso.

Rota nova:

```python
@router.post("/logout", response_model=GenericMessageResponse)
def logout(response: Response) -> GenericMessageResponse:
    """
    Encerra a sessão apagando o cookie.

    Não invalida o JWT no servidor — ele continua válido até expirar. Para
    matar todas as sessões de uma vez existe `users.token_version`, que a troca
    de senha já incrementa.
    """
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        path="/",
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=True,
    )
    return GenericMessageResponse(message="Sessão encerrada.")
```

- [ ] **Step 6: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_cookie_auth.py -v`
Expected: PASS (9 testes)

- [ ] **Step 7: Confirmar que nada de auth regrediu**

Run: `.venv/bin/python -m pytest tests/test_jwt_claims.py tests/test_session_invalidation.py tests/test_wardrobe_image_access.py -v`
Expected: PASS — todos continuam usando o header, que segue aceito.

- [ ] **Step 8: Suíte inteira + commit**

Run: `.venv/bin/python -m pytest -q` → 269 passed, 2 skipped.

```bash
git add app/core/config.py app/api/deps.py app/api/routes/auth.py tests/test_cookie_auth.py
git commit -m "feat(auth): sessão por cookie httpOnly, com header ainda aceito"
```

---
### Task 9: Cookie httpOnly — frontend

**Files:**
- Modify: `../miranda/lib/api.ts`
- Modify: `../miranda/components/AuthGuard.tsx`, `../miranda/components/AuthedImage.tsx`
- Modify: `../miranda/app/register/page.tsx`
- Create: `../miranda/app/verificar-email/page.tsx`
- Grep e ajuste todos os pontos que chamam `getToken`/`setToken`/`clearToken`

**Interfaces:**
- Consumes: cookie setado por `POST /api/auth/login`, `POST /api/auth/logout` (Task 8); resposta genérica de `register` (Task 6).
- Produces: nenhuma exportada para o backend.

**O que esta tarefa apaga.** Com cookie automático, `<img src>` volta a funcionar: o navegador manda o cookie sozinho em requisição de imagem, coisa que nunca fez com o header `Authorization`. Todo o mecanismo de `fetchImageObjectUrl` + cache de object URLs + `revokeImageCache` existe **apenas** para contornar aquela limitação e deve desaparecer. `AuthedImage` vira um `<img>`.

**A mudança que faz tudo funcionar:** toda chamada `fetch` precisa de `credentials: 'include'`. Sem isso o navegador não manda o cookie cross-origin (`localhost:3000` → `localhost:8000`) e tudo responde 401.

- [ ] **Step 1: Reescrever o bloco de token em `lib/api.ts`**

Substitua o cabeçalho e as funções `getToken`/`setToken`/`clearToken`/`isAuthenticated` por:

```typescript
// ─────────────────────────────────────────────────────────────────────
// Cliente HTTP do frontend para a API do Miranda (FastAPI).
//
// Sessão por cookie httpOnly:
//   O JWT não passa mais pelo JavaScript. Ele vive num cookie que o navegador
//   guarda e envia sozinho, e que `document.cookie` não enxerga — o que tira o
//   token do alcance de qualquer XSS.
//
//   Consequências práticas:
//   · toda requisição precisa de `credentials: 'include'`, senão o navegador
//     não manda o cookie para outra origem (3000 → 8000);
//   · não há como perguntar "estou logado?" ao cookie. Quem decide é a API:
//     `GET /api/auth/me` responde 200 ou 401, e é isso que o AuthGuard usa;
//   · sair da sessão é `POST /api/auth/logout` — só o servidor apaga o cookie.
// ─────────────────────────────────────────────────────────────────────
```

Remova `TOKEN_KEY`, `getToken`, `setToken`, `clearToken` e `isAuthenticated`. **Não deixe stubs.**

- [ ] **Step 2: Mandar o cookie em toda requisição**

Na função `request`, acrescente `credentials: 'include'` ao objeto passado ao `fetch`, e remova o bloco que monta o header `Authorization` a partir de `getToken()`. Na altura do `if (res.status === 401)`, troque `clearToken()` por nada — não há mais estado local para limpar; quem trata o 401 é o `AuthGuard`.

- [ ] **Step 3: Login e logout**

```typescript
export async function login(email: string, password: string): Promise<void> {
  // O cookie vem no Set-Cookie da resposta; não há nada a guardar aqui.
  await request<{ access_token: string }>('/api/auth/login', {
    method: 'POST',
    body: { email, password },
  })
}

export async function logout(): Promise<void> {
  // Só o servidor consegue apagar um cookie httpOnly.
  try {
    await request('/api/auth/logout', { method: 'POST' })
  } catch {
    // Falhar o logout no servidor não pode prender a pessoa na tela. O
    // redirecionamento acontece de qualquer jeito; o cookie expira sozinho.
  }
}
```

Ajuste as telas que chamavam `setToken(data.access_token)` para apenas `await login(...)`.

- [ ] **Step 4: Apagar o mecanismo de imagem autenticada**

Remova de `lib/api.ts`: `imageCache`, `fetchImageObjectUrl`, `revokeImageCache` e o bloco de comentário que os explicava.

Substitua `components/AuthedImage.tsx` inteiro por:

```typescript
// Imagem de peça.
//
// Já foi um componente com fetch + Bearer + object URL, porque o navegador não
// envia o header Authorization em requisição disparada por <img src>. Com a
// sessão em cookie httpOnly ele envia o cookie sozinho, então voltou a ser
// possível usar <img> direto — e todo o mecanismo de cache de blob some junto.
//
// O componente permanece (em vez de <img> espalhado) só para manter o
// fallback visual quando a imagem falha.

'use client'

import { useState } from 'react'

interface AuthedImageProps {
  src: string
  alt: string
  className?: string
  loading?: 'lazy' | 'eager'
}

export function AuthedImage({ src, alt, className, loading = 'lazy' }: AuthedImageProps) {
  const [failed, setFailed] = useState(false)

  if (failed) {
    return <div className={`${className ?? ''} bg-surface`} aria-label={alt} role="img" />
  }

  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading={loading}
      onError={() => setFailed(true)}
    />
  )
}
```

> Confira `bg-surface` contra `tailwind.config.ts` — use o token que existir para o fundo neutro do fallback atual.

- [ ] **Step 5: `AuthGuard` pergunta à API**

O guard já chama a API para validar a sessão. Garanta que ele: (a) não chame `getToken()`; (b) trate 401 redirecionando para `/`; (c) não tente limpar estado local.

- [ ] **Step 6: Tela de cadastro com a resposta genérica**

`app/register/page.tsx` precisa parar de esperar um usuário no corpo e de tratar o 409 (que não existe mais). Depois de cadastrar, mostre a mensagem que a API devolveu e oriente a pessoa a conferir o e-mail. **Não** diga "conta criada" — a resposta é a mesma quando o e-mail já existia, e afirmar criação seria mentir em metade dos casos.

- [ ] **Step 7: Tela de confirmação de e-mail**

Crie `app/verificar-email/page.tsx`: lê `?token=` da URL, chama `POST /api/auth/verify-email`, e mostra sucesso ou falha. É o destino do link do e-mail (`APP_BASE_URL/verificar-email?token=...`, definido na Task 5).

- [ ] **Step 8: Varredura — nenhum resquício**

Run:
```bash
cd ../miranda
grep -rn "localStorage\|getToken\|setToken\|clearToken\|fetchImageObjectUrl\|revokeImageCache" app components lib
```
Expected: nenhuma saída. Qualquer sobra é um ponto que vai dar 401 em runtime.

- [ ] **Step 9: Typecheck e build**

Run:
```bash
cd ../miranda && npx tsc --noEmit && npm run build
```
Expected: sem erros.

- [ ] **Step 10: Validação manual no navegador**

Com `docker compose up -d`, a API em `:8000` e o front em `:3000`:
1. Cadastrar uma conta nova → a tela mostra a mensagem genérica; o e-mail aparece no Mailpit (`localhost:8025`).
2. Abrir o link do e-mail → a tela de verificação confirma.
3. Entrar → no DevTools, **Application → Cookies** deve mostrar `miranda_session` com `HttpOnly` marcado, e **Local Storage vazio**.
4. Abrir o guarda-roupa → as imagens carregam (agora por `<img>` puro).
5. Sair → o cookie some e a home redireciona para o login.

- [ ] **Step 11: Commit**

```bash
cd ../miranda && git add -A && git commit -m "feat(auth): sessão por cookie httpOnly, sem token no localStorage"
```

> **Atenção:** `miranda/` é um repositório git SEPARADO de `miranda-api/`. Este commit vai nele.

---

### Task 10: Quota de peças por usuário

**Files:**
- Modify: `app/core/config.py`, `app/services/wardrobe_service.py`, `app/api/routes/wardrobe.py`, `.env.example`
- Test: `tests/test_wardrobe_quota.py`

**Interfaces:**
- Consumes: nada.
- Produces: `settings.MAX_ITEMS_PER_USER: int = 150`; `wardrobe_service.QuotaExceededError`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_wardrobe_quota.py`:

```python
"""
Quota de peças por usuário.

Sem teto, uma conta pode encher o disco e a tabela — e, como cada peça pode
passar pelo FashionCLIP, custar CPU indefinidamente. 150 é generoso para um
guarda-roupa real e finito para um script.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.clothing_item import ClothingItem
from app.models.enums import ClothingCategory
from app.models.user import User


def _png(cor=(120, 60, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), cor).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def client():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def dono(db):
    u = User(
        name="Dono da Quota",
        email=f"quota-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(ClothingItem).filter(ClothingItem.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(user.id), user.token_version)}"}


def _encher(db, user, quantas: int):
    """Cria peças direto no banco — mais rápido que passar pela rota."""
    for i in range(quantas):
        db.add(
            ClothingItem(
                user_id=user.id,
                name=f"peça {i}",
                category=ClothingCategory.CAMISA,
                image_path=f"seed_quota_{uuid.uuid4().hex}.png",
            )
        )
    db.commit()


def _cadastrar(client, user, nome="nova"):
    return client.post(
        "/api/wardrobe/items",
        headers=_auth(user),
        data={"name": nome, "category": "camisa"},
        files={"image": ("p.png", _png(), "image/png")},
    )


def test_below_the_quota_the_upload_works(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 5)
    _encher(db, dono, 4)
    assert _cadastrar(client, dono).status_code == 201


def test_at_the_quota_the_upload_is_refused(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 5)
    _encher(db, dono, 5)
    resp = _cadastrar(client, dono)
    assert resp.status_code == 409
    assert "150" in resp.text or "5" in resp.text, "a mensagem precisa dizer qual é o teto"


def test_the_refusal_does_not_create_the_item(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 3)
    _encher(db, dono, 3)
    _cadastrar(client, dono)
    assert db.query(ClothingItem).filter(ClothingItem.user_id == dono.id).count() == 3


def test_the_quota_is_per_user_not_global(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 2)
    _encher(db, dono, 2)

    outro = User(
        name="Outro",
        email=f"outro-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(outro)
    db.commit()
    db.refresh(outro)
    try:
        assert _cadastrar(client, dono).status_code == 409
        assert _cadastrar(client, outro).status_code == 201
    finally:
        db.query(ClothingItem).filter(ClothingItem.user_id == outro.id).delete()
        db.delete(outro)
        db.commit()


def test_deleting_frees_a_slot(client, db, dono, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ITEMS_PER_USER", 3)
    _encher(db, dono, 3)
    assert _cadastrar(client, dono).status_code == 409

    alguma = db.query(ClothingItem).filter(ClothingItem.user_id == dono.id).first()
    client.delete(f"/api/wardrobe/items/{alguma.id}", headers=_auth(dono))
    assert _cadastrar(client, dono).status_code == 201


def test_the_default_is_generous_but_finite():
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgresql+psycopg2://u:p@localhost:5432/db",
        JWT_SECRET_KEY="x" * 48,
        _env_file=None,
    )
    assert s.MAX_ITEMS_PER_USER == 150
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_wardrobe_quota.py -v`
Expected: FAIL — hoje o cadastro sempre passa; `test_at_the_quota_the_upload_is_refused` quebra com 201.

- [ ] **Step 3: Configuração**

```python
    # ── Quota de guarda-roupa ─────────────────────────────────────────
    # Teto de peças por usuário. Generoso para um guarda-roupa real (150 peças
    # cadastradas com foto é muito mais do que a maioria das pessoas tem) e
    # finito para um script: sem teto, uma conta enche o disco e a tabela, e
    # cada peça ainda pode custar uma passada de CPU pelo FashionCLIP.
    MAX_ITEMS_PER_USER: int = 150
```

- [ ] **Step 4: Erro de negócio e checagem no serviço**

Em `app/services/wardrobe_service.py`:

```python
class QuotaExceededError(Exception):
    """O usuário atingiu o teto de peças cadastradas."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"Limite de {limit} peças por conta atingido.")
        self.limit = limit


def _assert_within_quota(db: Session, *, user_id: uuid.UUID) -> None:
    """
    Confere a quota ANTES de gravar a imagem no disco.

    A ordem importa: validar depois do upload deixaria um arquivo órfão no
    storage a cada tentativa recusada — que é exatamente o recurso que a quota
    existe para proteger.
    """
    atuais = db.scalar(
        select(func.count(ClothingItem.id)).where(ClothingItem.user_id == user_id)
    )
    if (atuais or 0) >= settings.MAX_ITEMS_PER_USER:
        raise QuotaExceededError(settings.MAX_ITEMS_PER_USER)
```

Chame `_assert_within_quota(db, user_id=user_id)` como **primeira** linha de `create_item`, antes de qualquer leitura ou gravação da imagem.

- [ ] **Step 5: Traduzir para HTTP na rota**

Em `create_item` (`app/api/routes/wardrobe.py`), acrescente ao `try`:

```python
    except QuotaExceededError as exc:
        # 409 e não 403: não é falta de permissão, é conflito com o estado atual
        # da conta — e o caminho para resolver é apagar uma peça, não pedir
        # acesso.
        raise HTTPException(status_code=409, detail=str(exc))
```

- [ ] **Step 6: `.env.example`**

```
# Teto de peças por conta. Generoso para um guarda-roupa real, finito para um
# script — sem ele, uma conta pode encher o disco e a tabela.
MAX_ITEMS_PER_USER=150
```

- [ ] **Step 7: Rodar, suíte, commit**

Run: `.venv/bin/python -m pytest tests/test_wardrobe_quota.py -v` → PASS (6)
Run: `.venv/bin/python -m pytest -q` → 275 passed, 2 skipped.

```bash
git add app/core/config.py app/services/wardrobe_service.py app/api/routes/wardrobe.py .env.example tests/test_wardrobe_quota.py
git commit -m "feat(seguranca): quota de peças por usuário"
```

---
### Task 11: Rate limit em upload e `/analyze`

**Files:**
- Modify: `app/core/config.py`, `app/core/rate_limit.py`, `app/api/routes/wardrobe.py`, `.env.example`
- Test: `tests/test_wardrobe_rate_limit.py`

**Interfaces:**
- Consumes: `limiter` e `resolve_storage_uri` (Task 7).
- Produces: `settings.WARDROBE_UPLOAD_RATE_LIMIT: str = "60/hour"`, `ANALYZE_RATE_LIMIT: str = "40/hour"`; `rate_limit.user_or_ip_key`.

**A chave do limite é diferente da de auth.** Nas rotas de autenticação a cota é por par (IP, e-mail), porque não há usuário logado. Aqui há: a cota é **por usuário autenticado**, com queda para IP se não houver. Limitar só por IP puniria uma casa inteira atrás do mesmo NAT; limitar por usuário mira exatamente a conta que está abusando.

`/analyze` tem teto menor que o upload de propósito: ele roda o FashionCLIP, que é o gasto de CPU mais caro do projeto.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_wardrobe_rate_limit.py`:

```python
"""
Rate limit nas rotas caras de guarda-roupa.

Upload grava arquivo no disco; /analyze roda o FashionCLIP em CPU. As duas são
os caminhos mais caros da API e as únicas que um script consegue disparar em
rajada com uma conta válida.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core import rate_limit
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.clothing_item import ClothingItem
from app.models.user import User


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (24, 24), (10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


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
def _limite_baixo_e_contador_limpo(monkeypatch):
    """
    Baixa o teto para 3 e zera os contadores entre testes.

    Sem o reset, o primeiro teste consumiria a cota dos seguintes e a suíte
    passaria a depender da ordem de execução.
    """
    monkeypatch.setattr(settings, "WARDROBE_UPLOAD_RATE_LIMIT", "3/hour")
    monkeypatch.setattr(settings, "ANALYZE_RATE_LIMIT", "3/hour")
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def usuario(db):
    u = User(
        name="Quem Sobe",
        email=f"rl-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.query(ClothingItem).filter(ClothingItem.user_id == u.id).delete()
    db.delete(u)
    db.commit()


def _auth(u) -> dict:
    return {"Authorization": f"Bearer {create_access_token(str(u.id), u.token_version)}"}


def _sobe(client, u, i=0):
    return client.post(
        "/api/wardrobe/items",
        headers=_auth(u),
        data={"name": f"peça {i}", "category": "camisa"},
        files={"image": ("p.png", _png(), "image/png")},
    )


def test_normal_use_is_not_bothered(client, usuario):
    """Três uploads seguidos, dentro do teto, passam."""
    for i in range(3):
        assert _sobe(client, usuario, i).status_code == 201


def test_a_burst_beyond_the_ceiling_is_refused(client, usuario):
    for i in range(3):
        _sobe(client, usuario, i)
    excedente = _sobe(client, usuario, 99)
    assert excedente.status_code == 429


def test_the_ceiling_is_per_user_not_per_ip(client, db, usuario):
    """
    Uma casa inteira atrás do mesmo NAT compartilha IP. Se a cota fosse por IP,
    uma pessoa abusando derrubaria as outras junto.
    """
    for i in range(3):
        _sobe(client, usuario, i)
    assert _sobe(client, usuario, 99).status_code == 429

    vizinho = User(
        name="Vizinho",
        email=f"viz-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(vizinho)
    db.commit()
    db.refresh(vizinho)
    try:
        # Mesmo cliente, mesmo IP, outro usuário: cota própria.
        assert _sobe(client, vizinho, 0).status_code == 201
    finally:
        db.query(ClothingItem).filter(ClothingItem.user_id == vizinho.id).delete()
        db.delete(vizinho)
        db.commit()


def test_analyze_has_its_own_ceiling(client, usuario):
    """
    /analyze não compartilha cota com o upload: são custos diferentes e um não
    deve consumir o outro.
    """
    for _ in range(3):
        client.post(
            "/api/wardrobe/items/analyze",
            headers=_auth(usuario),
            files={"image": ("p.png", _png(), "image/png")},
        )
    excedente = client.post(
        "/api/wardrobe/items/analyze",
        headers=_auth(usuario),
        files={"image": ("p.png", _png(), "image/png")},
    )
    assert excedente.status_code == 429


def test_the_defaults_are_generous_for_a_person(client):
    """
    O teto existe contra script, não contra gente. Cadastrar 60 peças numa hora
    já é muito mais do que qualquer pessoa faz.
    """
    from app.core.config import Settings

    s = Settings(
        DATABASE_URL="postgresql+psycopg2://u:p@localhost:5432/db",
        JWT_SECRET_KEY="x" * 48,
        _env_file=None,
    )
    assert s.WARDROBE_UPLOAD_RATE_LIMIT == "60/hour"
    assert s.ANALYZE_RATE_LIMIT == "40/hour"
```

> **Nota para quem executa:** confirme que `limiter.reset()` existe na versão do slowapi instalada (0.1.10). Se não existir, zere o storage direto — para `memory://`, recriando o limiter; para Redis, `redis-cli FLUSHDB` no banco 0. O importante é que os testes não dependam da ordem.

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_wardrobe_rate_limit.py -v`
Expected: FAIL — hoje não há teto; o excedente devolve 201, não 429.

- [ ] **Step 3: Configuração**

```python
    # Tetos das rotas caras de guarda-roupa, por USUÁRIO autenticado (ver
    # `rate_limit.user_or_ip_key`).
    #
    # Upload grava arquivo no disco; /analyze roda o FashionCLIP em CPU e é o
    # gasto mais caro do projeto — por isso o teto menor. Os dois números são
    # generosos para uma pessoa (60 peças cadastradas numa hora é muito mais do
    # que qualquer um faz) e apertados para um script.
    WARDROBE_UPLOAD_RATE_LIMIT: str = "60/hour"
    ANALYZE_RATE_LIMIT: str = "40/hour"
```

- [ ] **Step 4: Chave por usuário em `app/core/rate_limit.py`**

```python
def user_or_ip_key(request: Request) -> str:
    """
    Chave de cota para rotas autenticadas: o usuário, com queda para o IP.

    Diferente das rotas de auth, aqui existe usuário logado — e é ele o eixo
    certo. Limitar por IP puniria uma casa inteira atrás do mesmo NAT por causa
    de uma pessoa, e um atacante com uma conta trocaria de IP para contornar.

    A queda para IP cobre o caso em que a dependency de autenticação ainda não
    rodou quando a key_func é chamada.
    """
    user_id = getattr(request.state, "current_user_id", None)
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"
```

Para que `request.state.current_user_id` exista, faça `get_current_user` (em `app/api/deps.py`) depositar o id assim que resolver o usuário:

```python
    request.state.current_user_id = str(user.id)
```

- [ ] **Step 5: Aplicar nas rotas**

Em `app/api/routes/wardrobe.py`, decore `create_item` e `analyze_item`. Ambas precisam receber `request: Request` na assinatura (exigência do slowapi):

```python
@router.post("", response_model=ClothingItemPublic, status_code=201)
@limiter.limit(lambda: settings.WARDROBE_UPLOAD_RATE_LIMIT, key_func=user_or_ip_key)
async def create_item(
    request: Request,
    ...
```

> O `lambda` é necessário para o teto ser lido em tempo de requisição — se fosse a string direta, o `monkeypatch` dos testes não teria efeito, porque o decorator congela o valor no import. Confirme que a versão do slowapi aceita callable em `limit()`; se não aceitar, use a string direta e ajuste os testes para não depender de monkeypatch (aí eles precisam estourar o teto real, o que é lento — prefira o callable).

- [ ] **Step 6: `.env.example`**

```
# Tetos das rotas caras de guarda-roupa, por usuário autenticado.
WARDROBE_UPLOAD_RATE_LIMIT=60/hour
ANALYZE_RATE_LIMIT=40/hour
```

- [ ] **Step 7: Rodar, suíte, commit**

Run: `.venv/bin/python -m pytest tests/test_wardrobe_rate_limit.py -v` → PASS (5)
Run: `.venv/bin/python -m pytest -q` → 280 passed, 2 skipped.

```bash
git add app/core app/api/routes/wardrobe.py .env.example tests/test_wardrobe_rate_limit.py
git commit -m "feat(seguranca): rate limit por usuário em upload e analyze"
```

---

### Task 12: Hash perceptual anti-reenvio

**Files:**
- Modify: `app/models/clothing_item.py`, `app/services/image_validation.py`, `app/services/wardrobe_service.py`, `app/api/routes/wardrobe.py`
- Create: `alembic/versions/0006_clothing_item_image_hash.py`
- Test: `tests/test_image_dedupe.py`

**Interfaces:**
- Consumes: `QuotaExceededError` como precedente de erro de negócio (Task 10).
- Produces: `image_validation.perceptual_hash(contents: bytes) -> str`; `ClothingItem.image_hash: str | None`; `wardrobe_service.DuplicateImageError`.

**Algoritmo: dHash 8x8, implementado em ~15 linhas sobre o Pillow.** Sem dependência nova: `imagehash` traria `scipy` junto, o que é desproporcional para uma função deste tamanho. dHash compara cada pixel com o vizinho da direita numa miniatura 9x8 em tons de cinza; o resultado é um inteiro de 64 bits em hexadecimal.

**Por que perceptual e não SHA-256:** um hash criptográfico muda inteiro se um único byte mudar, então recomprimir o JPEG, redimensionar ou salvar de novo já burla a checagem. O dHash sobrevive a essas transformações — que são exatamente as que um script usaria para reenviar a mesma foto de graça.

**A comparação é por igualdade exata do dHash, não por distância de Hamming.** Distância pegaria também fotos *parecidas* — duas camisas brancas diferentes, por exemplo — e recusar o cadastro de uma peça legítima é pior do que deixar passar um reenvio. Igualdade exata erra para o lado seguro.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_image_dedupe.py`:

```python
"""
Recusa de reenvio da mesma imagem pelo mesmo usuário.

A quota (Task 10) limita quantas peças cabem; isto limita quão barato é
enchê-la. Sem esta checagem, um script sobe a mesma foto 150 vezes e ocupa o
guarda-roupa inteiro com um único arquivo.

Por que perceptual e não SHA-256: um hash criptográfico muda inteiro se um byte
mudar, então recomprimir ou redimensionar já burla. O dHash sobrevive a essas
transformações.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image

from app.services.image_validation import perceptual_hash


def _bytes(img: Image.Image, formato="PNG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=formato, **kw)
    return buf.getvalue()


def _foto(seed: int = 0) -> Image.Image:
    """Uma imagem com estrutura — dHash de imagem chapada é degenerado."""
    img = Image.new("RGB", (200, 260), (240, 235, 220))
    for y in range(260):
        for x in range(200):
            if (x + y + seed) % 37 < 12:
                img.putpixel((x, y), (30 + seed * 40, 60, 90))
    return img


# ── O hash em si ────────────────────────────────────────────────────────────
def test_the_same_bytes_hash_the_same():
    b = _bytes(_foto())
    assert perceptual_hash(b) == perceptual_hash(b)


def test_the_hash_survives_recompression():
    """O caminho barato de burlar: salvar de novo com outra qualidade."""
    img = _foto()
    a = perceptual_hash(_bytes(img, "JPEG", quality=95))
    b = perceptual_hash(_bytes(img, "JPEG", quality=60))
    assert a == b


def test_the_hash_survives_resizing():
    img = _foto()
    a = perceptual_hash(_bytes(img))
    b = perceptual_hash(_bytes(img.resize((100, 130))))
    assert a == b


def test_the_hash_survives_a_format_change():
    img = _foto()
    assert perceptual_hash(_bytes(img, "PNG")) == perceptual_hash(_bytes(img, "JPEG", quality=92))


def test_different_images_hash_differently():
    assert perceptual_hash(_bytes(_foto(0))) != perceptual_hash(_bytes(_foto(1)))


def test_the_hash_has_a_stable_shape():
    """16 caracteres hexadecimais: 64 bits de dHash. A coluna tem esse tamanho."""
    h = perceptual_hash(_bytes(_foto()))
    assert len(h) == 16
    int(h, 16)  # levanta se não for hexadecimal


def test_a_corrupt_image_does_not_raise():
    """
    Bytes inválidos já são recusados por `validate_image_bytes` antes daqui.
    Se ainda assim chegarem, o hash devolve None em vez de derrubar o upload.
    """
    assert perceptual_hash(b"isto nao e uma imagem") is None
```

E os testes de rota, no mesmo arquivo:

```python
# ── A recusa na rota ────────────────────────────────────────────────────────
# (fixtures `client`, `db`, `usuario`, `_auth` — copie o bloco de
# tests/test_wardrobe_quota.py; são os mesmos e não vale inventar outros)

def test_reuploading_the_same_image_is_refused(client, db, usuario):
    img = _bytes(_foto())
    primeira = client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("a.png", img, "image/png")},
    )
    assert primeira.status_code == 201

    segunda = client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "outro nome", "category": "calca"},
        files={"image": ("b.png", img, "image/png")},
    )
    assert segunda.status_code == 409
    assert "imagem" in segunda.text.lower()


def test_a_recompressed_resend_is_also_refused(client, db, usuario):
    """O caminho barato de burlar precisa fechar junto."""
    img = _foto()
    client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("a.png", _bytes(img, "JPEG", quality=95), "image/jpeg")},
    )
    segunda = client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("b.jpg", _bytes(img, "JPEG", quality=55), "image/jpeg")},
    )
    assert segunda.status_code == 409


def test_another_user_may_upload_the_same_image(client, db, usuario):
    """
    A checagem é POR USUÁRIO. Duas pessoas podem legitimamente ter a mesma foto
    de catálogo da mesma peça, e recusar isso seria um bug de produto.
    """
    from app.models.user import User

    img = _bytes(_foto())
    client.post(
        "/api/wardrobe/items",
        headers=_auth(usuario),
        data={"name": "camisa", "category": "camisa"},
        files={"image": ("a.png", img, "image/png")},
    )

    outro = User(
        name="Outro",
        email=f"dup-{uuid.uuid4().hex}@exemplo.com",
        hashed_password="$2b$12$" + "x" * 53,
    )
    db.add(outro)
    db.commit()
    db.refresh(outro)
    try:
        resp = client.post(
            "/api/wardrobe/items",
            headers=_auth(outro),
            data={"name": "camisa", "category": "camisa"},
            files={"image": ("a.png", img, "image/png")},
        )
        assert resp.status_code == 201
    finally:
        from app.models.clothing_item import ClothingItem

        db.query(ClothingItem).filter(ClothingItem.user_id == outro.id).delete()
        db.delete(outro)
        db.commit()


def test_a_different_image_still_goes_through(client, db, usuario):
    for i, nome in enumerate(["a.png", "b.png"]):
        resp = client.post(
            "/api/wardrobe/items",
            headers=_auth(usuario),
            data={"name": f"peça {i}", "category": "camisa"},
            files={"image": (nome, _bytes(_foto(i)), "image/png")},
        )
        assert resp.status_code == 201
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_image_dedupe.py -v`
Expected: FAIL — `ImportError: cannot import name 'perceptual_hash'`

- [ ] **Step 3: `perceptual_hash` em `app/services/image_validation.py`**

```python
def perceptual_hash(contents: bytes) -> str | None:
    """
    dHash de 64 bits da imagem, em hexadecimal (16 caracteres).

    ── Por que perceptual e não SHA-256 ────────────────────────────────────
    Um hash criptográfico muda inteiro se um único byte mudar, então
    recomprimir o JPEG, redimensionar ou só salvar de novo já burlaria a
    checagem de reenvio — e essas são exatamente as transformações que um
    script usaria para encher a quota de graça.

    ── Como funciona ───────────────────────────────────────────────────────
    Reduz para 9x8 em tons de cinza e compara cada pixel com o vizinho da
    direita: 8 comparações por linha, 8 linhas, 64 bits. O resultado depende da
    ESTRUTURA da imagem (onde ela fica mais clara e mais escura), não dos bytes
    — por isso sobrevive à recompressão.

    Implementado à mão de propósito: a biblioteca `imagehash` traria `scipy`
    junto, o que é desproporcional para quinze linhas.

    Returns:
        O hash, ou `None` se os bytes não abrirem como imagem. Não lança: a
        validação de imagem já roda antes, e um erro aqui não pode derrubar um
        upload que ela aprovou.
    """
    try:
        with Image.open(io.BytesIO(contents)) as img:
            reduzida = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(reduzida.getdata())
    except Exception:  # noqa: BLE001
        return None

    bits = 0
    for linha in range(8):
        base = linha * 9
        for coluna in range(8):
            bits <<= 1
            if pixels[base + coluna] > pixels[base + coluna + 1]:
                bits |= 1
    return f"{bits:016x}"
```

> Confirme que `io` e `Image` já estão importados no arquivo (estão, por causa de `validate_image_bytes`).

- [ ] **Step 4: Coluna no modelo**

Em `app/models/clothing_item.py`, depois de `image_path`:

```python
    # dHash de 64 bits da imagem, em hexadecimal. Usado para recusar o reenvio
    # da MESMA foto pelo mesmo usuário — ver `image_validation.perceptual_hash`.
    #
    # Nulo é permitido: as peças cadastradas antes desta coluna não têm hash, e
    # recalculá-las exigiria reabrir cada arquivo do storage. Peça sem hash
    # simplesmente não participa da checagem de duplicata.
    image_hash: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
```

- [ ] **Step 5: Migration `alembic/versions/0006_clothing_item_image_hash.py`**

```python
"""Hash perceptual da imagem da peça.

Revision ID: 0006_clothing_item_image_hash
Revises: 0005_email_verification
"""

from alembic import op
import sqlalchemy as sa

revision = "0006_clothing_item_image_hash"
down_revision = "0005_email_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nulo para as peças já cadastradas: recalcular exigiria reabrir cada
    # arquivo do storage, e uma peça sem hash apenas não participa da checagem.
    op.add_column("clothing_items", sa.Column("image_hash", sa.String(16), nullable=True))
    # O índice é por (user_id, image_hash) porque a consulta é sempre "esta
    # pessoa já tem esta imagem?" — nunca "alguém já tem".
    op.create_index(
        "ix_clothing_items_user_image_hash", "clothing_items", ["user_id", "image_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_clothing_items_user_image_hash", table_name="clothing_items")
    op.drop_column("clothing_items", "image_hash")
```

Run: `.venv/bin/python -m alembic upgrade head`

- [ ] **Step 6: Checagem no serviço**

Em `app/services/wardrobe_service.py`:

```python
class DuplicateImageError(Exception):
    """Esta imagem já está cadastrada nesta conta."""

    def __init__(self) -> None:
        super().__init__(
            "Esta imagem já está cadastrada no seu guarda-roupa. "
            "Use outra foto ou edite a peça existente."
        )
```

Em `create_item`, depois de ler e validar os bytes da imagem e ANTES de gravar no storage:

```python
    image_hash = perceptual_hash(contents)
    if image_hash is not None:
        ja_existe = db.scalar(
            select(ClothingItem.id).where(
                ClothingItem.user_id == user_id,
                ClothingItem.image_hash == image_hash,
            )
        )
        if ja_existe is not None:
            # Antes de gravar: recusar depois deixaria o arquivo órfão no disco,
            # que é justamente o recurso a proteger.
            raise DuplicateImageError()
```

E persista `image_hash=image_hash` ao criar o `ClothingItem`.

- [ ] **Step 7: HTTP na rota**

```python
    except DuplicateImageError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
```

- [ ] **Step 8: Rodar, suíte, commit**

Run: `.venv/bin/python -m pytest tests/test_image_dedupe.py -v` → PASS (11)
Run: `.venv/bin/python -m pytest -q` → 291 passed, 2 skipped.

```bash
git add app/models/clothing_item.py app/services alembic/versions/0006_clothing_item_image_hash.py app/api/routes/wardrobe.py tests/test_image_dedupe.py
git commit -m "feat(seguranca): recusa reenvio da mesma imagem por hash perceptual"
```

---
### Task 13: Investigação vestido/saia e calibração

**Files:**
- Modify: `app/services/ai/labels.py` (apenas os prompts de `vestido` e `saia`)
- Create: `scripts/investigate_dress_skirt.py`
- Create: `docs/superpowers/relatorio-vestido-saia.md`
- Modify: `tests/test_analysis_regression.py`

**Interfaces:**
- Consumes: nada de tarefas anteriores.
- Produces: relatório com números antes/depois. Nenhuma interface de código nova.

**Ponto de partida — leia antes de mexer.** Os prompts JÁ foram tornados distintivos numa investigação anterior: `vestido` enfatiza peça única cobrindo tronco e pernas, `saia` enfatiza só da cintura para baixo. Já existe teste de regressão com verdade-base (`tests/test_analysis_regression.py`: `12.jpg → vestido`, `4.jpg → saia`, `2.jpg → calca`, e `p(vestido) > p(saia)` para `12.jpg`). **Esta tarefa não é "adicionar prompts distintivos do zero" — é medir se o que está lá resolve, e melhorar com dado.**

**A pergunta a responder:** foi ambiguidade da imagem ou confusão sistemática da classe? A diferença é observável: se for sistemática, `saia` vence `vestido` em VÁRIAS imagens de vestido e a margem entre as duas é pequena em todas. Se for ambiguidade, o erro é isolado e as demais têm margem folgada.

⚠️ **A primeira execução baixa ~600 MB de pesos do FashionCLIP** e cada imagem leva alguns segundos em CPU. 33 imagens são alguns minutos.

- [ ] **Step 1: Escrever o script de investigação**

Crie `scripts/investigate_dress_skirt.py`:

```python
#!/usr/bin/env python3
"""
Investiga a confusão entre `vestido` e `saia` no zero-shot do FashionCLIP.

Não é um teste: é o instrumento para responder UMA pergunta — o erro relatado
foi ambiguidade daquela imagem, ou confusão sistemática entre as duas classes?

A diferença é observável nos números:
  · SISTEMÁTICA — `saia` vence em várias imagens de vestido, e a margem entre
    as duas é estreita em quase todas. O problema está nos prompts.
  · AMBIGUIDADE — o erro é isolado e as outras imagens separam com folga. O
    problema está naquela foto.

Uso:
    PYTHONPATH=. .venv/bin/python scripts/investigate_dress_skirt.py

Imprime, para cada imagem de test-images/: o vencedor geral, p(vestido),
p(saia) e a margem entre as duas.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai.fashion_clip import classify  # noqa: E402
from app.services.ai.labels import CATEGORY_CANDIDATES  # noqa: E402

TEST_IMAGES = Path(__file__).resolve().parent.parent / "test-images"


def main() -> None:
    imagens = sorted(
        TEST_IMAGES.glob("*.jpg"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0
    )
    if not imagens:
        sys.exit(f"Nenhuma imagem em {TEST_IMAGES}")

    print(f"{'imagem':<10} {'vencedor':<12} {'p(vest)':>8} {'p(saia)':>8} {'margem':>8}")
    print("─" * 50)

    confusoes = []
    for caminho in imagens:
        scored = dict(classify(str(caminho), CATEGORY_CANDIDATES))
        vencedor = max(scored, key=scored.get)
        pv, ps = scored.get("vestido", 0.0), scored.get("saia", 0.0)
        margem = abs(pv - ps)
        print(f"{caminho.name:<10} {vencedor:<12} {pv:>8.3f} {ps:>8.3f} {margem:>8.3f}")
        if {vencedor} & {"vestido", "saia"} and margem < 0.15:
            confusoes.append((caminho.name, vencedor, pv, ps, margem))

    print("\n── Casos de margem estreita (< 0.15) entre vestido e saia ──")
    if not confusoes:
        print("  nenhum — as duas classes separam com folga em todas as imagens")
    for nome, vencedor, pv, ps, margem in confusoes:
        print(f"  {nome}: venceu {vencedor} · vestido={pv:.3f} saia={ps:.3f} margem={margem:.3f}")

    print(
        "\nLeitura: muitos casos de margem estreita = confusão SISTEMÁTICA "
        "(mexa nos prompts).\nPoucos ou nenhum = ambiguidade da imagem "
        "específica (documente e siga)."
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Rodar o diagnóstico ANTES de mexer em nada**

Run: `PYTHONPATH=. .venv/bin/python scripts/investigate_dress_skirt.py | tee /tmp/vestido-saia-antes.txt`

Guarde a saída: é a linha de base contra a qual a melhoria será medida. **Não pule este passo** — sem o "antes" não há como afirmar que algo melhorou.

- [ ] **Step 3: Decidir com base no número, não na intuição**

- Se houver **três ou mais** imagens com margem < 0.15 → confusão sistemática. Siga ao Step 4.
- Se houver **zero, uma ou duas** → ambiguidade pontual. **Pule o Step 4**, registre isso no relatório e vá ao Step 6. Mexer em prompts que já separam bem só arrisca regressão nas outras classes.

- [ ] **Step 4: Refinar os prompts (só se o Step 3 apontou confusão sistemática)**

Em `app/services/ai/labels.py`, substitua as duas listas. O eixo de distinção é **cobertura do tronco**: um vestido tem parte de cima, uma saia não. Torne isso explícito em todos os prompts, e acrescente a negação — o CLIP responde a ela:

```python
    Candidate(
        ClothingCategory.VESTIDO.value,
        [
            "a photo of a dress",
            "a photo of a one-piece dress that covers both the torso and the legs",
            "a photo of a dress with a bodice and a skirt attached as a single garment",
            "a photo of a full-length dress from the shoulders to the hem",
            "a photo of a sleeveless slip dress covering the chest and the legs",
            "a photo of a maxi dress with a neckline and shoulder straps",
        ],
    ),
```

```python
    Candidate(
        ClothingCategory.SAIA.value,
        [
            "a photo of a skirt",
            "a photo of a skirt covering only the lower body from the waist down",
            "a photo of a skirt with a waistband and no bodice, no sleeves and no neckline",
            "a photo of a midi skirt worn without a top",
            "a photo of an a-line skirt, a bottom garment that does not cover the torso",
        ],
    ),
```

- [ ] **Step 5: Rodar de novo e comparar**

Run: `PYTHONPATH=. .venv/bin/python scripts/investigate_dress_skirt.py | tee /tmp/vestido-saia-depois.txt`
Run: `diff /tmp/vestido-saia-antes.txt /tmp/vestido-saia-depois.txt`

**Critério de aceite:** nenhuma imagem pode ter mudado de vencedor para pior, e a margem de `12.jpg` (o caso que originou a investigação) precisa ter aumentado ou ficado igual. Se alguma outra classe regrediu, **reverta os prompts** — trocar um erro de vestido/saia por um erro de camisa/malha não é progresso.

- [ ] **Step 6: Rodar a calibração completa**

Run: `PYTHONPATH=. .venv/bin/python scripts/calibrate_fashion_clip.py test-images/ | tee /tmp/calibracao-depois.txt`

Confira que os limiares de `config.py` (`FASHION_CLIP_THRESHOLD_CATEGORIA=0.80`) continuam fazendo sentido contra a distribuição observada.

- [ ] **Step 7: Escrever o relatório**

Crie `docs/superpowers/relatorio-vestido-saia.md` com: o veredito (ambiguidade ou sistemática) e o número que o sustenta; a tabela antes/depois das duas classes; se os prompts mudaram e por quê; e se a confiança e o acerto melhoraram, com os valores. **Se não melhorou, diga que não melhorou** — um relatório que só reporta sucesso não serve para decidir nada.

- [ ] **Step 8: Reforçar o teste de regressão**

Em `tests/test_analysis_regression.py`, acrescente uma asserção de MARGEM ao teste existente, para que uma futura mudança de prompts que aperte a distinção seja pega:

```python
def test_the_dress_beats_the_skirt_by_a_clear_margin(model_available):
    """
    Não basta vencer: a margem precisa ser folgada. Uma vitória por 0.01 é
    ruído, e a próxima mudança de prompt a inverteria sem ninguém notar.
    """
    scored = dict(classify(str(TEST_IMAGES / "12.jpg"), CATEGORY_CANDIDATES))
    margem = scored["vestido"] - scored["saia"]
    assert margem > 0.15, f"margem estreita demais: {margem:.3f}"
```

> Ajuste o limiar `0.15` para o valor que o Step 5 realmente mediu, menos uma folga. Não invente um número que o modelo não alcança.

- [ ] **Step 9: Suíte + commit**

Run: `.venv/bin/python -m pytest -q` → 292 passed, 2 skipped (ou 291 + 1, conforme o teste novo).

```bash
git add app/services/ai/labels.py scripts/investigate_dress_skirt.py docs/superpowers/relatorio-vestido-saia.md tests/test_analysis_regression.py
git commit -m "fix(ia): investiga e reforça a distinção entre vestido e saia"
```

---

### Task 14: Segunda camada de defesa e limitação documentada

**Files:**
- Modify: `app/services/ai/look_generation.py`
- Test: `tests/test_look_generation.py` (acrescentar)

**Interfaces:**
- Consumes: `_structure_is_valid`, `_parse_reply` (já existem).
- Produces: nenhuma nova.

**Contexto — isto restaura algo que a migração perdeu.** Antes da migração para a API do Claude, `look_generation.py` abria com um bloco `⚠️ LIMITAÇÃO CONHECIDA` explicando que a composição CONFIA na categoria armazenada, e havia um teste (`test_mislabeled_dress_as_skirt_is_treated_as_bottom`) cobrindo o caso. A reescrita (commit `3cc04c4`) apagou os dois. Confirmado com `git show 188bee8:app/services/ai/look_generation.py`.

O problema continua existindo, e agora com um agravante: o modelo recebe `categoria` no JSON e decide a partir dela, então uma categoria errada contamina a composição igual — só que agora sem nenhum aviso no código.

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_look_generation.py`:

```python
# ── Categoria errada vinda da análise (limitação conhecida) ─────────────────
# A composição confia na `category` gravada na peça. Se a análise rotulou um
# vestido como `saia`, ele é uma peça de baixo daqui para a frente — e o
# resultado parece um erro de composição, mas nasce na categorização.
#
# Estes testes fixam o COMPORTAMENTO nesse caso: previsível e estruturalmente
# válido, ainda que baseado num dado errado.
def test_a_dress_mislabelled_as_a_skirt_is_treated_as_a_bottom(monkeypatch):
    """
    O vestido rotulado como saia vira peça de baixo e aceita peça de cima.
    Isso NÃO é um bug da composição: é o dado que chegou errado. O que a
    composição garante é não produzir uma estrutura inválida por causa disso.
    """
    guarda_roupa = [
        piece("d_errado", "saia", peso="leve"),   # é um vestido, veio como saia
        piece("t1", "camisa", peso="leve"),
        piece("f1", "calcado", peso="leve"),
    ]
    _stub_api(monkeypatch, [reply([{
        "label": "I",
        "items": [
            {"item_id": "d_errado", "role": "peça de baixo"},
            {"item_id": "t1", "role": "peça de cima"},
            {"item_id": "f1", "role": "calçado"},
        ],
        "commentary": "Uma frase qualquer.",
    }])])

    result = generate_daily_look(guarda_roupa, MILD_DAY, ocasiao="dia_a_dia")
    assert result["unavailable"] is False
    assert len(result["looks"]) == 1
    papeis = {i["role"] for i in result["looks"][0]["items"]}
    assert "peça de baixo" in papeis and "peça de cima" in papeis


def test_the_structural_guard_still_holds_with_a_wrong_category(monkeypatch):
    """
    A rede de segurança continua valendo: com a categoria errada, o modelo NÃO
    consegue produzir vestido + peça de baixo, porque a peça deixou de contar
    como vestido. A estrutura sai válida mesmo com o dado errado.
    """
    guarda_roupa = [
        piece("d_errado", "saia", peso="leve"),
        piece("b1", "calca", peso="leve"),
        piece("t1", "camisa", peso="leve"),
    ]
    # Duas peças de baixo: tem de ser descartado.
    _stub_api(monkeypatch, [reply([{
        "label": "I",
        "items": [
            {"item_id": "d_errado", "role": "peça de baixo"},
            {"item_id": "b1", "role": "peça de baixo"},
            {"item_id": "t1", "role": "peça de cima"},
        ],
        "commentary": "x",
    }])])

    result = generate_daily_look(guarda_roupa, MILD_DAY, ocasiao="dia_a_dia")
    assert result["looks"] == []
    assert result["unavailable"] is True


def test_the_known_limitation_is_documented_in_the_module():
    """
    Trava de documentação. Esta limitação já foi apagada uma vez, na reescrita
    que trocou o motor de regras pela API (commit 3cc04c4), junto com o teste
    que a cobria. Este teste existe para que a próxima reescrita não a apague
    em silêncio de novo.
    """
    import app.services.ai.look_generation as lg

    doc = lg.__doc__ or ""
    assert "LIMITAÇÃO CONHECIDA" in doc
    assert "categoria" in doc.lower()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_look_generation.py -v -k "mislabelled or limitation or wrong_category"`
Expected: FAIL — `test_the_known_limitation_is_documented_in_the_module` quebra; a docstring não fala disso.

- [ ] **Step 3: Restaurar a limitação na docstring de `look_generation.py`**

Acrescente ao final da docstring do módulo, antes do fechamento:

```
── ⚠️ LIMITAÇÃO CONHECIDA: a composição confia na CATEGORIA ────────────────
A estrutura de um look — nunca vestido com peça de baixo, nunca duas peças de
baixo — é decidida pela `category` gravada em cada peça, que vem da análise de
imagem (FashionCLIP) ou do preenchimento manual. Não há reconhecimento visual
em tempo de composição: nem aqui, nem no modelo, que recebe a categoria como
texto e acredita nela.

Portanto **a qualidade da composição depende da qualidade da categorização**.
Um vestido rotulado por engano como `saia` vira peça de baixo e será combinado
com uma peça de cima. Isso parece um erro de composição e não é: o dado chegou
errado.

Três camadas atenuam, nenhuma resolve:
  1. os prompts de `vestido` e `saia` em `labels.py` foram escritos para
     separar as duas classes pelo eixo que as distingue — cobertura do tronco
     (ver `docs/superpowers/relatorio-vestido-saia.md`);
  2. o limiar de confiança (`FASHION_CLIP_THRESHOLD_CATEGORIA`) deixa o campo
     NULO em vez de chutar quando o modelo está em dúvida;
  3. `_structure_is_valid` reconfere a saída e descarta o look que violar a
     estrutura — mas ele julga pelas MESMAS categorias, então não enxerga um
     vestido escondido atrás do rótulo `saia`.

A correção de verdade é o usuário poder corrigir a categoria na ficha da peça,
o que a interface já permite. Nenhum classificador é perfeito e este projeto
não pretende esconder isso.
```

- [ ] **Step 4: Rodar e ver passar**

Run: `.venv/bin/python -m pytest tests/test_look_generation.py -v`
Expected: PASS (todos, incluindo os 3 novos)

- [ ] **Step 5: Suíte + commit**

Run: `.venv/bin/python -m pytest -q` → 295 passed, 2 skipped.

```bash
git add app/services/ai/look_generation.py tests/test_look_generation.py
git commit -m "docs(look): restaura a limitação de categoria perdida na migração"
```

---
### Task 15: README — instalação, checklist de deploy e pendências

**Files:**
- Modify: `../README.md` (índice; seção de instalação; seção 16.5; seção nova de deploy)

**Interfaces:** nenhuma — documentação.

⚠️ **O README está FORA de qualquer repositório git.** Não pode ser commitado e não tem desfazer. Faça backup antes (`cp ../README.md /tmp/README.antes-task15`), localize cada edição por CONTEÚDO e nunca por número de linha.

- [ ] **Step 1: Backup**

Run: `cp ../README.md /tmp/README.antes-task15 && wc -l /tmp/README.antes-task15`

- [ ] **Step 2: Seção de instalação — Docker, Mailpit e Redis**

Depois da seção que instala o PostgreSQL, e no mesmo tom, acrescente:

````markdown
### Dependências em contêiner (Mailpit e Redis)

Duas dependências locais sobem juntas com Docker Compose. **Nenhuma das duas é
obrigatória** para a API funcionar — sem elas o envio de e-mail cai no log e o
rate limiter volta para memória. Elas existem para o comportamento local ser o
mesmo de produção.

```bash
cd ~/projects/my/miranda-folder/miranda-api
docker compose up -d      # sobe Mailpit e Redis
docker compose ps         # confere
docker compose down       # derruba quando quiser
```

| Serviço | Para quê | Onde ver |
|---|---|---|
| **Mailpit** | Captura os e-mails que a aplicação envia. **Não manda nada para fora da máquina** — nenhum endereço real recebe coisa alguma. | http://localhost:8025 |
| **Redis** | Storage dos contadores de rate limit. Em memória, cada worker teria a própria cota. | `docker exec miranda-redis redis-cli ping` |

Depois de subir, aponte o `.env` para eles:

```
EMAIL_BACKEND=smtp
RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0
```

Sem Docker, deixe `EMAIL_BACKEND=console` (os e-mails aparecem no log do
servidor) e `RATE_LIMIT_STORAGE_URI=memory://` — correto com um worker só.
````

Acrescente a entrada correspondente ao índice, seguindo a numeração existente.

- [ ] **Step 3: Seção nova — checklist de deploy**

Antes da seção 16 (Segurança), acrescente uma seção numerada nova:

````markdown
## 17. Checklist de deploy

Tudo abaixo só se aplica quando o projeto sair da máquina local. Enquanto roda
em `localhost`, nada disto é necessário — e algumas coisas quebrariam o
ambiente de desenvolvimento se ligadas cedo demais.

### Bloqueadores — sem isto, não publique

- [ ] **HTTPS.** Nada mais nesta lista protege credenciais em trânsito sem TLS.
      É o item que precede todos os outros.
- [ ] **`AUTH_COOKIE_SECURE=true`.** Sem isso o cookie de sessão viaja em texto
      claro. Está `false` por padrão porque o navegador descartaria um cookie
      `Secure` em `http://localhost`.
- [ ] **`AUTH_COOKIE_SAMESITE`.** Se frontend e API ficarem em domínios
      diferentes, `lax` não basta: será preciso `none`, que **exige** `Secure`.
      Mesmo domínio (ou subdomínios do mesmo site) mantém `lax`, que é mais
      seguro.
- [ ] **`JWT_SECRET_KEY` própria e longa.** Gere com
      `python -c "import secrets; print(secrets.token_urlsafe(48))"`. O boot já
      recusa o placeholder do `.env.example`.
- [ ] **`CORS_ORIGINS` com as origens reais**, nunca `*`. Com cookie de sessão,
      CORS deixou de ser cosmético: é o que impede outro site de ler a resposta
      de uma requisição autenticada. O boot já recusa `*` com credenciais.
- [ ] **`SECURITY_HSTS_ENABLED=true`**, uma vez que o HTTPS esteja de pé.
- [ ] **`EMAIL_BACKEND=resend`** com `RESEND_API_KEY` e um **domínio
      verificado**. Sem domínio verificado o Resend só entrega no endereço do
      dono da conta — o que basta para testar, não para ter usuários.
- [ ] **`APP_BASE_URL` com o endereço público do frontend.** É a base dos links
      que vão dentro dos e-mails; apontando para `localhost`, ninguém consegue
      confirmar o cadastro.
- [ ] **`RATE_LIMIT_STORAGE_URI` num Redis de verdade**, se houver mais de um
      worker. Em memória, o teto vale N vezes mais do que aparenta.
- [ ] **Banco fora do contêiner efêmero, com backup.** O `docker-compose.yml`
      deste projeto sobe Redis SEM persistência de propósito (são contadores) e
      **não** sobe o Postgres — ele é nativo na máquina de desenvolvimento.

### Recomendado antes de abrir para outras pessoas

- [ ] **`REQUIRE_VERIFIED_EMAIL=true`.** Vem `false` porque uma trava que
      depende do servidor de e-mail prenderia o próprio dono. Com entrega
      confiável, ligue — e verifique antes as contas que já existem, senão elas
      ficam trancadas para fora.
- [ ] **Revisar `MAX_ITEMS_PER_USER`**, `WARDROBE_UPLOAD_RATE_LIMIT` e
      `ANALYZE_RATE_LIMIT` contra o uso real.
- [ ] **`ENABLE_PROMPT_CACHE=true`** se o volume de gerações sustentar a janela
      de 5 minutos — ver seção 12. Com pouco tráfego, ligar isto AUMENTA o custo.
- [ ] **Vigiar o custo da API do Claude.** Não há controle de quota; o
      acompanhamento é pelo log (`custo_estimado_usd` por chamada).
- [ ] **`HOST=0.0.0.0`** só quando a API estiver atrás de um proxy reverso.

### Fica pendente mesmo depois de hospedar

- [ ] **Refresh token.** O access token de 12 h é um meio-termo entre segurança
      e ter que logar de novo. O arranjo correto é access de minutos com
      refresh longo e revogável. A troca de senha já invalida todas as sessões
      via `users.token_version`.
- [ ] **Quota de custo da API do Claude por usuário.** Hoje o rate limit protege
      CPU e disco, mas a geração de look é a rota que gasta dinheiro e não tem
      teto próprio.
````

- [ ] **Step 4: Reescrever a seção 16.5 ("O que ainda falta")**

Ela hoje lista cinco itens abertos. Quatro foram fechados por este plano. Substitua o bloco inteiro por:

````markdown
### 16.5 O que ainda falta

Fechados neste ciclo, com o item da revisão original entre parênteses:

- ✅ **Envio de e-mail no reset de senha** (#1). O token vai por e-mail e não
  aparece mais no log — ver seção 12 e `app/services/email/`.
- ✅ **Enumeração de contas no cadastro** (#4). A resposta é genérica e o aviso
  de "e-mail já em uso" chega por e-mail ao dono. O tempo dos dois caminhos foi
  igualado (o bcrypt roda sempre), porque o relógio também era um canal.
- ✅ **Token no `localStorage`.** Migrado para cookie `httpOnly`, inalcançável
  por JavaScript. A contrapartida — CSRF, já que o navegador passa a mandar a
  credencial sozinho — é coberta por `SameSite=Lax` e CORS restrito.
- ✅ **Rate limiter em memória.** Agora em Redis, com queda para memória e aviso
  no log se o Redis não responder.

Ainda em aberto:

- **Refresh token.** Ver o checklist de deploy (seção 17).
- **HTTPS.** Não é um item de código: depende de onde o projeto for hospedado.
  Está no checklist.
````

- [ ] **Step 5: Conferir que nada ficou desatualizado**

Run:
```bash
grep -n -i "localStorage\|409\|já está cadastrado\|memory://\|token vai para o log" ../README.md
```
Cada ocorrência sobrevivente precisa estar correta no mundo novo. `localStorage` só pode aparecer em contexto histórico ("já foi"); `memory://` só como fallback documentado.

- [ ] **Step 6: Conferir o índice**

Run: `grep -n "^## " ../README.md`
Todas as seções numeradas precisam existir no índice do topo, com a âncora certa.

---

## Validação final

Depois da Task 15, antes do relatório. **Não é uma tarefa de subagente** — é a conferência do controlador.

- [ ] **Suíte completa:** `.venv/bin/python -m pytest -q` → esperado ~295 passed, 2 skipped (baseline 216 + 2).
- [ ] **Boot sem nada opcional:** `docker compose down` e, com `EMAIL_BACKEND=console`, `ANTHROPIC_API_KEY=` vazia e Redis fora, a API sobe e `GET /api/health` (ou `/docs`) responde.
- [ ] **Fluxo manual de ponta a ponta**, com `docker compose up -d`:
  1. Cadastro → mensagem genérica; e-mail de confirmação no Mailpit.
  2. Cadastro com o MESMO e-mail → mesma mensagem; aviso ao dono no Mailpit; nenhuma conta nova.
  3. Link de confirmação → conta verificada.
  4. Login → cookie `miranda_session` com `HttpOnly` no DevTools; `localStorage` vazio.
  5. Upload de peça → funciona; imagens carregam por `<img>`.
  6. Reenviar a MESMA foto → 409.
  7. Estourar o teto de upload → 429.
  8. Gerar look → funciona.
  9. Logout → cookie some; rota protegida devolve 401.
- [ ] **`git grep -n "sk-ant-\|re_[A-Za-z0-9]\{20,\}"`** não retorna nenhuma chave real.
- [ ] **`git status`** não lista `.env`.

---

## Self-review deste plano

**Cobertura do spec:**
Frente 1 → Tasks 13, 14. Frente 2 → Tasks 10, 11, 12. Frente 3 → Tasks 1, 2, 3. Frente 4 → Tasks 4, 5, 6. Frente 5 → Tasks 7, 8, 9, 15. Entrega final → Validação final acima e o relatório do controlador.

**Riscos que este plano assume conscientemente:**
1. **Task 9 depende da Task 6** (a tela de cadastro precisa tratar a resposta genérica). Entre as duas, o frontend fica temporariamente inconsistente. A Task 6 avisa disso no Step 4.
2. **A Task 13 pode concluir "não havia o que consertar"** e isso é um resultado válido, não uma falha. O Step 3 dá o critério numérico para decidir.
3. **A Task 11 depende de o slowapi aceitar callable em `limit()`.** O Step 5 registra o plano B.
4. **Os números de teste esperados em cada tarefa são estimativas.** O que importa é a suíte não regredir; divergência de contagem não é falha.
5. **Docker é requisito das Tasks 1, 3 (Step 7), 7 e da validação manual** — está instalado e verificado (29.7.2, Compose v5.5.0).
