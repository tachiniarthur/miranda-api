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
            "normalmente. Se esqueceu sua senha, use a opção de recuperação na "
            "tela de login. Se não foi você, também não é preciso fazer nada."
            + _ASSINATURA
        ),
    )
