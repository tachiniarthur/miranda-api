"""Schemas Pydantic do domínio de autenticação."""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.common_passwords import COMMON_PASSWORD_MESSAGE, is_common_password


def _reject_common_password(value: str) -> str:
    """
    Validator compartilhado pelo cadastro e pela troca de senha.

    O `min_length=8` garante tamanho, não imprevisibilidade: "senha123" passa no
    comprimento e é dos primeiros palpites de qualquer ataque de dicionário.
    """
    if is_common_password(value):
        raise ValueError(COMMON_PASSWORD_MESSAGE)
    return value


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    _no_common_password = field_validator("password")(_reject_common_password)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    # Resposta deliberadamente sem nenhum campo variável: só a mensagem
    # genérica. O token de redefinição NÃO trafega por aqui (ver a rota), e
    # qualquer campo que só aparecesse para e-mails existentes recriaria o
    # oráculo de enumeração que a mensagem genérica existe para fechar.
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)

    _no_common_password = field_validator("new_password")(_reject_common_password)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class MessageResponse(BaseModel):
    message: str
