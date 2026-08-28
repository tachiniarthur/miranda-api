"""Rotas de autenticação: cadastro, login, recuperação e redefinição de senha."""

# NOTA: este módulo deliberadamente NÃO usa `from __future__ import annotations`.
# O decorator @limiter.limit do slowapi embrulha o endpoint com functools.wraps,
# que não copia `__globals__`. Com as anotações adiadas (em string), o FastAPI
# tentaria resolvê-las contra os globals do slowapi — onde os schemas deste
# módulo não existem — e passaria a tratar `payload` como query param, quebrando
# o corpo das requisições. Com anotações reais o problema não existe.

import logging
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter, stash_auth_identity
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.schemas.user import UserPublic
from app.services import auth_service
from app.services.email.messages import render_password_reset
from app.services.email.sender import send_email

logger = logging.getLogger("miranda.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=201,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """
    Recebe um cadastro.

    Responde SEMPRE a mesma coisa, exista ou não o e-mail. Quando já existe,
    nada é criado e quem recebe o aviso é o dono do endereço — o único canal que
    só ele lê. O custo é de experiência: quem esqueceu que já tinha conta não vê
    mais o erro na tela. É o preço de fechar a enumeração (item #4).

    A conta também não sai daqui autenticada: entrar exige passar pelo login,
    que é onde a senha é conferida.
    """
    user = auth_service.register_or_notify(
        db, name=payload.name, email=payload.email, password=payload.password
    )
    if user is not None:
        auth_service.send_verification_email(db, user=user)

    # Mesma resposta nos dois casos. Não devolvemos o usuário criado: o corpo
    # precisa ser idêntico exista ou não a conta, e um objeto de usuário só
    # existiria em um dos caminhos.
    return MessageResponse(
        message=(
            "Cadastro recebido. Se este e-mail ainda não tiver conta, "
            "enviamos um link de confirmação."
        )
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    Autentica o usuário, seta o cookie de sessão e retorna o JWT.

    O corpo continua trazendo `access_token` porque clientes não-navegador
    (scripts, um app nativo) não têm por que lidar com cookie — e o header
    `Authorization` segue aceito nas rotas protegidas.
    """
    access_token = auth_service.authenticate_user(
        db, email=payload.email, password=payload.password
    )

    # httpOnly: o JavaScript não lê este valor, então um XSS não leva a sessão
    # embora. SameSite=Lax é a contrapartida obrigatória — com cookie, o
    # navegador manda a credencial sozinho, e sem isso qualquer site poderia
    # disparar requisições autenticadas (CSRF).
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=access_token,
        httponly=True,
        samesite=settings.AUTH_COOKIE_SAMESITE,
        secure=settings.AUTH_COOKIE_SECURE,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return TokenResponse(access_token=access_token)


@router.post("/logout", response_model=MessageResponse)
def logout(response: Response) -> MessageResponse:
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
    return MessageResponse(message="Sessão encerrada.")


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def forgot_password(
    request: Request,
    response: Response,
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    """
    Inicia o fluxo de recuperação de senha.

    Gera um token de redefinição de uso único e o envia POR E-MAIL ao dono do
    endereço. Responde sempre de forma genérica — mesma mensagem, mesmos campos,
    exista ou não o e-mail — para não revelar quais endereços estão cadastrados.

    O token nunca vai na resposta HTTP nem no log: quem faz o pedido não é
    necessariamente o dono do e-mail, e qualquer um desses dois canais entregaria
    a conta a quem soubesse o endereço.

    A garantia sobre o log não é declaratória — quem a cumpre é
    `_mascara_tokens` em `app/services/email/sender.py`, que substitui o valor
    de qualquer `token=` antes de o corpo ir para o log. Até esta rodada a
    afirmação acima era falsa com o backend padrão (`console`), que despejava o
    corpo inteiro (achado A1).

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


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def reset_password(
    request: Request,
    response: Response,
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Redefine a senha usando um token de redefinição válido."""
    auth_service.reset_password(
        db, token=payload.token, new_password=payload.new_password
    )
    return MessageResponse(message="Senha redefinida com sucesso.")


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def verify_email(
    request: Request,
    response: Response,
    payload: VerifyEmailRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """
    Confirma o endereço a partir do token recebido por e-mail.

    Sob rate limit porque o token é adivinhável por força bruta em tese — 256
    bits tornam isso irrealista, mas o teto custa nada e fecha a porta.
    """
    if not auth_service.confirm_email_verification(db, token=payload.token):
        raise HTTPException(400, "Link de confirmação inválido ou expirado.")
    return MessageResponse(message="E-mail confirmado.")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def resend_verification(
    request: Request,
    response: Response,
    payload: ResendVerificationRequest,
    db: Session = Depends(get_db),
) -> MessageResponse:
    """
    Reenvia o e-mail de confirmação.

    Resposta genérica sempre: exista ou não a conta, esteja ou não verificada, o
    corpo é o mesmo. Variar aqui transformaria esta rota num verificador de
    e-mails cadastrados — a mesma falha que `forgot-password` já evita.
    """
    user = auth_service.get_user_by_email(db, email=payload.email)
    if user is not None:
        auth_service.send_verification_email(db, user=user)
    return MessageResponse(
        message=(
            "Se este e-mail estiver cadastrado e ainda não confirmado, "
            "enviamos um novo link."
        )
    )


@router.get("/me", response_model=UserPublic)
def me(current_user: User = Depends(get_current_user)) -> User:
    """Retorna os dados do usuário autenticado (valida o token)."""
    return current_user
