"""
Lógica de negócio de autenticação, separada das rotas HTTP.

As funções aqui levantam `AuthError` com uma mensagem e um status HTTP
sugerido; a camada de rotas converte isso em `HTTPException`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    TOKEN_VERSION_CLAIM,
    create_access_token,
    decode_token_payload,
    generate_reset_token,
    hash_password,
    hash_reset_token,
    verify_password,
)
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User


class AuthError(Exception):
    """Erro de autenticação com status HTTP associado."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.lower()))


def register_user(db: Session, *, name: str, email: str, password: str) -> User:
    """Cria um novo usuário, rejeitando e-mail duplicado."""
    email = email.lower()
    if _get_user_by_email(db, email) is not None:
        raise AuthError(409, "Este e-mail já está cadastrado.")

    user = User(
        name=name.strip(),
        email=email,
        hashed_password=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, *, email: str, password: str) -> str:
    """
    Valida credenciais e retorna um JWT de acesso.

    O bcrypt é executado SEMPRE, inclusive quando o e-mail não existe — nesse
    caso contra `DUMMY_PASSWORD_HASH`, um hash fixo de mesmo custo. A versão
    anterior usava `user is None or not verify_password(...)`, e o
    curto-circuito do `or` fazia o e-mail inexistente responder sem gastar os
    ~250 ms do bcrypt. A diferença era mensurável numa única requisição, então a
    mensagem genérica de erro não escondia nada: o relógio dizia quais e-mails
    estavam cadastrados.

    Por isso `verify_password` é chamado antes de qualquer decisão, e o
    resultado só é combinado com a existência do usuário depois.
    """
    user = _get_user_by_email(db, email)
    hashed = user.hashed_password if user is not None else DUMMY_PASSWORD_HASH

    # Executado incondicionalmente: é este trabalho de CPU que iguala os tempos.
    senha_confere = verify_password(password, hashed)

    # Mensagem genérica de propósito: não revela se o e-mail existe.
    if user is None or not senha_confere:
        raise AuthError(401, "E-mail ou senha inválidos.")
    return create_access_token(str(user.id), token_version=user.token_version)


def get_user_from_access_token(db: Session, token: str) -> User | None:
    """
    Resolve o usuário a partir de um JWT de acesso válido.

    Além da assinatura, do prazo e das claims `iss`/`aud`, confere a versão de
    sessão: a claim `ver` do token precisa bater com `users.token_version`. Como
    a troca de senha incrementa a coluna, todos os tokens emitidos antes dela
    passam a ser recusados aqui — é o que derruba as sessões abertas.
    """
    payload = decode_token_payload(token, expected_type="access")
    if payload is None:
        return None

    try:
        uid = uuid.UUID(payload["sub"])
    except (ValueError, KeyError):
        return None

    user = db.get(User, uid)
    if user is None:
        return None

    # Token sem a claim (emitido antes desta mudança) é recusado: não há como
    # saber de qual versão de sessão ele é, e assumir a atual reabriria o
    # próprio buraco que a claim fecha.
    token_version = payload.get(TOKEN_VERSION_CLAIM)
    if not isinstance(token_version, int) or token_version != user.token_version:
        return None

    return user


# ── Redefinição de senha ──────────────────────────────────────────────

def _revoke_outstanding_reset_tokens(
    db: Session, *, user_id: uuid.UUID, now: datetime
) -> None:
    """
    Marca como usados todos os tokens de redefinição ainda pendentes do usuário.

    Chamado tanto ao emitir um token novo (só o último pedido vale) quanto ao
    concluir uma redefinição (nenhum token sobrevive à troca de senha). Como
    `used_at` preenchido já reprova o token na validação, revogar e consumir são
    a mesma operação — não há um segundo estado a checar.
    """
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )


def create_reset_token_for_email(db: Session, *, email: str) -> str | None:
    """
    Gera um token de redefinição de senha para o e-mail informado.

    Retorna o token EM CLARO se o usuário existir, ou None caso contrário —
    apenas o hash vai para o banco. O valor retornado nunca deve ir para a
    resposta HTTP: quem chama é responsável por entregá-lo ao dono do e-mail.
    A rota sempre responde de forma genérica, para não revelar quais e-mails
    estão cadastrados.
    """
    user = _get_user_by_email(db, email)
    if user is None:
        return None

    now = datetime.now(timezone.utc)
    _revoke_outstanding_reset_tokens(db, user_id=user.id, now=now)

    raw_token = generate_reset_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_reset_token(raw_token),
            expires_at=now
            + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
        )
    )
    db.commit()
    return raw_token


def reset_password(db: Session, *, token: str, new_password: str) -> None:
    """
    Troca a senha do usuário associado a um token de redefinição válido.

    O token é aceito uma única vez: ao concluir, ele e qualquer outro token
    pendente do mesmo usuário são carimbados como usados. A versão de sessão do
    usuário também é incrementada, o que invalida os JWTs de acesso já emitidos.
    """
    # `with_for_update()` trava a linha do token até o fim desta transação.
    #
    # Sem a trava havia uma corrida real (verificada, não teórica): dois pedidos
    # simultâneos com o MESMO token liam `used_at IS NULL` antes de qualquer um
    # gravar, os dois passavam pela checagem e os dois trocavam a senha. Com a
    # trava, o segundo pedido fica bloqueado no SELECT; quando o primeiro
    # confirma, ele relê a linha já com `used_at` preenchido e é recusado.
    record = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == hash_reset_token(token))
        .with_for_update()
    )
    now = datetime.now(timezone.utc)

    # Mensagem única para token inexistente, já usado/revogado e expirado: nada
    # aqui deve ajudar a distinguir os casos.
    if record is None or record.used_at is not None or record.expires_at <= now:
        raise AuthError(400, "Token de redefinição inválido ou expirado.")

    user = db.get(User, record.user_id)
    if user is None:
        raise AuthError(400, "Token de redefinição inválido ou expirado.")

    user.hashed_password = hash_password(new_password)
    # Derruba todas as sessões abertas: os JWTs já emitidos carregam a versão
    # antiga e passam a ser recusados em get_user_from_access_token. Sem isto,
    # quem tivesse roubado um token seguiria dentro por até o prazo de
    # expiração, mesmo depois de a vítima redefinir a senha.
    user.token_version += 1
    db.add(user)
    # Cobre o próprio `record` (ainda pendente) e os demais tokens do usuário.
    _revoke_outstanding_reset_tokens(db, user_id=user.id, now=now)
    db.commit()
