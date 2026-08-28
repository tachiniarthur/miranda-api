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
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as _MIMEMessage

import httpx

from app.core.config import settings

logger = logging.getLogger("miranda.email")

RESEND_ENDPOINT = "https://api.resend.com/emails"
_TIMEOUT_SECONDS = 10.0

# Qualquer `token=<valor>` numa URL do corpo. Os tokens de redefinição e de
# verificação são `secrets.token_urlsafe(32)`: alfanumérico mais `-` e `_`.
_TOKEN_NA_URL = re.compile(r"(token=)[A-Za-z0-9_\-]+")


def _mascara_tokens(texto: str) -> str:
    """
    Substitui o valor de qualquer `token=` do corpo por um marcador.

    O corpo do e-mail de redefinição carrega a URL com o token, e o backend
    `console` — que é o PADRÃO — despejava o corpo inteiro no log. Quem tivesse
    leitura do log (operador, agregador, sidecar, backup, colega com acesso ao
    servidor) tomava qualquer conta: bastava disparar um pedido de redefinição
    para o e-mail alvo e copiar o token de lá. O token vale 30 minutos e a
    redefinição não exige a senha antiga.

    Mascara-se o VALOR e preserva-se o `token=`: o log continua mostrando a
    forma da URL, que é o que serve para depurar, sem o segredo.
    """
    return _TOKEN_NA_URL.sub(r"\1[REDIGIDO]", texto)


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

    O corpo passa por `_mascara_tokens` antes de ir ao log: o log é um ativo que
    muita gente lê, e o corpo carrega o token que dá acesso à conta.
    """
    logger.info(
        "[e-mail não enviado: backend=console] para=%s assunto=%s\n%s",
        message.to,
        message.subject,
        _mascara_tokens(message.text),
    )
    return True


def _send_smtp(message: EmailMessage) -> bool:
    try:
        # A construção do MIME entra no try de propósito: um nome de exibição
        # digitado pelo usuário (ex.: `render_password_reset(user.name, ...)`)
        # pode conter um surrogate solto ou uma quebra de linha, e tanto
        # `set_content` quanto a atribuição de cabeçalho levantam nesse caso
        # (UnicodeEncodeError, ValueError). Isso não é diferente, para quem
        # chamou, de um SMTP fora do ar: o cadastro não pode cair por causa
        # disso.
        mime = _MIMEMessage()
        mime["From"] = settings.EMAIL_FROM
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.text)

        with smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=_TIMEOUT_SECONDS
        ) as smtp:
            smtp.send_message(mime)
    except Exception as exc:  # noqa: BLE001
        # Largo de propósito: smtplib levanta uma família grande (OSError,
        # SMTPException, socket.timeout...) e a própria construção do MIME
        # pode levantar (UnicodeEncodeError, ValueError) — nenhuma delas
        # justifica derrubar o cadastro de quem está do outro lado. O tipo da
        # exceção vai no log para o operador distinguir "mensagem malformada"
        # de "servidor fora do ar".
        logger.warning(
            "Falha ao enviar e-mail por SMTP (%s:%s) para %s: %s: %s",
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            message.to,
            type(exc).__name__,
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
        # com 4xx. A mensagem da API é útil e vai inteira para o log — mas
        # `.text` decodifica o corpo da resposta na hora, o que também pode
        # levantar (corpo mal-formado), e isso já está fora do try/except que
        # cerca `httpx.post`. Mesma regra: um corpo problemático não pode virar
        # HTTP 500 numa rota de auth.
        try:
            body = response.text
        except Exception as exc:  # noqa: BLE001
            body = f"<corpo ilegível: {type(exc).__name__}: {exc}>"
        logger.warning(
            "Resend recusou o envio para %s (HTTP %s): %s",
            message.to,
            response.status_code,
            body,
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
