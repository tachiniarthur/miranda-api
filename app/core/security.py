"""
Primitivas de segurança: hashing de senha (bcrypt) e geração/validação de JWT.

Usamos a biblioteca `bcrypt` diretamente (em vez de passlib) para evitar
problemas de compatibilidade conhecidos entre passlib e versões recentes do
bcrypt, e por ser uma dependência mais enxuta.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.core.config import settings


# ── Senha ─────────────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """Gera o hash bcrypt de uma senha em texto puro."""
    # bcrypt opera sobre bytes e trunca em 72 bytes; codificamos em utf-8.
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


# Hash bcrypt fixo (custo 12, o mesmo dos hashes reais) usado quando o login
# recebe um e-mail que não existe. Pré-computado e gravado como constante para
# que a aplicação não gaste ~250 ms de bcrypt no boot só para gerá-lo.
#
# Nenhuma senha conhecida corresponde a ele — foi gerado a partir de uma frase
# descartada e jamais é comparado com uma senha real. Sua única função é fazer o
# login executar o MESMO trabalho de CPU quando o e-mail não existe e quando ele
# existe mas a senha está errada. Sem isso, o `or` de curto-circuito devolvia a
# resposta sem rodar bcrypt para e-mail inexistente, e a diferença de tempo
# (~250 ms) revelava, numa única requisição, quais e-mails estão cadastrados.
DUMMY_PASSWORD_HASH = "$2b$12$57gOFo9kdkJFdLZZ6qm5Se43Q493360x2sGxa1Ix9ZyuS6jJMUbde"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha em texto puro corresponde ao hash armazenado."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        # Hash malformado no banco — trata como senha inválida em vez de estourar.
        return False


# ── JWT ───────────────────────────────────────────────────────────────

# Nome da claim que carrega a versão de sessão do usuário (ver item #7).
TOKEN_VERSION_CLAIM = "ver"


def _create_token(
    subject: str, expires_delta: timedelta, token_type: str, token_version: int
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,          # id do usuário (uuid em string)
        "type": token_type,      # hoje sempre "access"
        # Versão de sessão do usuário no momento da emissão. Trocar a senha
        # incrementa o valor no banco, o que invalida de uma vez todos os
        # tokens já emitidos (ver get_user_from_access_token).
        TOKEN_VERSION_CLAIM: token_version,
        "iss": settings.JWT_ISSUER,      # quem emitiu
        "aud": settings.JWT_AUDIENCE,    # para quem o token vale
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str, token_version: int = 0) -> str:
    """
    Cria um JWT de acesso para o usuário informado.

    `token_version` deve ser o `users.token_version` atual: é ele que permite
    derrubar as sessões abertas quando a senha muda.
    """
    return _create_token(
        subject=user_id,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_type="access",
        token_version=token_version,
    )


# ── Token de redefinição de senha ─────────────────────────────────────
# Deliberadamente NÃO é um JWT. Um JWT stateless não pode ser de uso único nem
# revogado: vale até expirar, aceita reapresentação e sobrevive à própria troca
# de senha. O token aqui é um valor opaco aleatório cujo SHA-256 é persistido em
# `password_reset_tokens` (ver app/models/password_reset_token.py).

# 32 bytes = 256 bits de entropia. `token_urlsafe` devolve ~43 caracteres
# seguros para URL/JSON.
_RESET_TOKEN_BYTES = 32


def generate_reset_token() -> str:
    """Gera um token de redefinição opaco e criptograficamente aleatório."""
    return secrets.token_urlsafe(_RESET_TOKEN_BYTES)


def hash_reset_token(token: str) -> str:
    """
    Devolve o SHA-256 hexadecimal do token, que é o que vai para o banco.

    SHA-256 basta (em vez de bcrypt) porque o token não é escolhido por humano:
    são 256 bits aleatórios, sem espaço de busca a proteger.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# O token de verificação usa exatamente o mesmo tamanho e a mesma função de
# hash do de reset. São o mesmo problema — segredo opaco de uso único cujo
# valor em claro não pode ser persistido — e resolvê-los diferente só criaria
# duas superfícies para auditar.
def generate_verification_token() -> str:
    """Gera um token de verificação de e-mail opaco e aleatório."""
    return secrets.token_urlsafe(_RESET_TOKEN_BYTES)


def hash_verification_token(token: str) -> str:
    """SHA-256 hexadecimal do token de verificação — o que vai para o banco."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str, expected_type: str) -> str | None:
    """
    Decodifica e valida um JWT, garantindo o tipo esperado.
    Retorna o `sub` (id do usuário) em caso de sucesso, ou None se o token for
    inválido, expirado ou de tipo incorreto.
    """
    payload = decode_token_payload(token, expected_type)
    return None if payload is None else payload["sub"]


def decode_token_payload(token: str, expected_type: str) -> dict[str, Any] | None:
    """
    Como `decode_token`, mas devolve o payload inteiro.

    Necessário para quem precisa de mais do que o `sub` — hoje, a claim de
    versão de sessão conferida em `get_user_from_access_token`.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            # Passar `audience`/`issuer` faz o PyJWT EXIGIR as claims: um token
            # sem `aud`/`iss`, ou com valores diferentes, é recusado. Tokens
            # emitidos antes desta mudança param de valer — os usuários
            # precisam entrar de novo, o que é o comportamento desejado.
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
        )
    except jwt.PyJWTError:
        return None

    if payload.get("type") != expected_type:
        return None

    # `sub` é garantido aqui para que quem chama possa indexá-lo sem checar.
    if not isinstance(payload.get("sub"), str):
        return None
    return payload
