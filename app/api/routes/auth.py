"""Rotas de autenticação: cadastro, login, recuperação e redefinição de senha."""

# NOTA: este módulo deliberadamente NÃO usa `from __future__ import annotations`.
# O decorator @limiter.limit do slowapi embrulha o endpoint com functools.wraps,
# que não copia `__globals__`. Com as anotações adiadas (em string), o FastAPI
# tentaria resolvê-las contra os globals do slowapi — onde os schemas deste
# módulo não existem — e passaria a tratar `payload` como query param, quebrando
# o corpo das requisições. Com anotações reais o problema não existe.

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.rate_limit import AUTH_RATE_LIMIT, limiter, stash_auth_identity
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.user import UserPublic
from app.services import auth_service
from app.services.auth_service import AuthError

logger = logging.getLogger("miranda.auth")

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    dependencies=[Depends(stash_auth_identity)],
)
@limiter.limit(AUTH_RATE_LIMIT)
def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Cadastra um novo usuário e já retorna um JWT de acesso."""
    try:
        auth_service.register_user(
            db, name=payload.name, email=payload.email, password=payload.password
        )
        access_token = auth_service.authenticate_user(
            db, email=payload.email, password=payload.password
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return TokenResponse(access_token=access_token)


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
    """Autentica o usuário e retorna um JWT de acesso."""
    try:
        access_token = auth_service.authenticate_user(
            db, email=payload.email, password=payload.password
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return TokenResponse(access_token=access_token)


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

    Gera um token de redefinição de uso único associado ao usuário. Responde
    sempre de forma genérica — mesma mensagem, mesmos campos, exista ou não o
    e-mail — para não revelar quais e-mails estão cadastrados.

    O token NUNCA vai na resposta HTTP: quem faz o pedido não é necessariamente
    o dono do e-mail, e devolvê-lo entregaria a conta a qualquer um que
    soubesse o endereço.

    TODO (antes de expor à internet): ainda não há servidor de e-mail, então o
    token é registrado no log do servidor em nível WARNING, para permitir testar
    o fluxo localmente. Isso deixa um segredo em texto puro nos logs — aceitável
    só enquanto a API roda na máquina do desenvolvedor. Substituir por envio de
    e-mail e remover o log antes de qualquer ambiente exposto.
    """
    reset_token = auth_service.create_reset_token_for_email(db, email=payload.email)
    if reset_token is not None:
        logger.warning(
            "[DEV] Token de redefinição gerado para %s: %s "
            "(substituir por envio de e-mail antes de publicar)",
            payload.email,
            reset_token,
        )
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
    try:
        auth_service.reset_password(
            db, token=payload.token, new_password=payload.new_password
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return MessageResponse(message="Senha redefinida com sucesso.")


@router.get("/me", response_model=UserPublic)
def me(current_user: User = Depends(get_current_user)) -> User:
    """Retorna os dados do usuário autenticado (valida o token)."""
    return current_user
