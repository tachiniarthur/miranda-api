"""Rotas de autenticação: cadastro, login, recuperação e redefinição de senha."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
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

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> TokenResponse:
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


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Autentica o usuário e retorna um JWT de acesso."""
    try:
        access_token = auth_service.authenticate_user(
            db, email=payload.email, password=payload.password
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return TokenResponse(access_token=access_token)


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> ForgotPasswordResponse:
    """
    Inicia o fluxo de recuperação de senha.

    Gera um token de redefinição associado ao usuário. Responde sempre de forma
    genérica (mesmo se o e-mail não existir) para não revelar quais e-mails
    estão cadastrados.

    ATENÇÃO / TODO: por enquanto o token é retornado diretamente na resposta da
    API apenas para permitir testar o fluxo localmente. Em produção isso é uma
    falha de segurança — o token deve ser ENVIADO POR E-MAIL para o usuário, e
    a resposta da API não deve conter o token.
    """
    reset_token = auth_service.create_reset_token_for_email(db, email=payload.email)
    return ForgotPasswordResponse(
        message=(
            "Se este e-mail estiver cadastrado, enviamos instruções para "
            "redefinir a senha."
        ),
        reset_token=reset_token,  # remover em produção (enviar por e-mail).
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    payload: ResetPasswordRequest, db: Session = Depends(get_db)
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
