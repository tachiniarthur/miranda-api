"""
Testes da invalidação de sessões na troca de senha (item #7) e do uso único do
token de redefinição sob concorrência (item #2).

O elo entre os dois: o reset de senha só cumpre o propósito se, além de o token
morrer depois de usado (#2), os JWTs já emitidos deixarem de valer (#7). Sem a
segunda metade, quem roubou um token continuava dentro por até
ACCESS_TOKEN_EXPIRE_MINUTES mesmo depois de a vítima redefinir a senha — ou
seja, exatamente no momento em que ela estava reagindo à invasão.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import User
from app.services import auth_service
from app.services.auth_service import AuthError

SENHA = "senha-original-desta-conta-1"
SENHA_NOVA = "senha-nova-desta-conta-2"


@pytest.fixture(scope="module")
def _engine_ok():
    from app.core.database import engine

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")


@pytest.fixture
def db(_engine_ok):
    from app.core.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    u = User(
        name="Usuária",
        email=f"sessao-{uuid.uuid4().hex}@exemplo.com",
        hashed_password=hash_password(SENHA),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.delete(u)
    db.commit()


@pytest.fixture
def client():
    return TestClient(app)


def _me(client, token: str):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


# ── #7: prazo do token de acesso ──────────────────────────────────────

def test_prazo_do_access_token_e_curto_o_bastante():
    """
    Sem refresh token, o prazo é o ÚNICO limite para um token vazado.

    Era de 7 dias (10080 min). O teto de 24 h aqui existe para que um aumento
    silencioso volte a falhar.
    """
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60 * 24
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 60 * 12


# ── #7: a claim de versão de sessão ───────────────────────────────────

def test_token_emitido_carrega_a_versao_de_sessao(db, user):
    import jwt

    token = auth_service.authenticate_user(db, email=user.email, password=SENHA)
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
    )
    assert payload["ver"] == user.token_version


def test_usuario_novo_comeca_na_versao_zero(user):
    assert user.token_version == 0


def test_token_valido_da_acesso(client, db, user):
    token = auth_service.authenticate_user(db, email=user.email, password=SENHA)
    r = _me(client, token)
    assert r.status_code == 200
    assert r.json()["email"] == user.email


def test_token_com_versao_antiga_e_recusado(client, db, user):
    """O cerne do item #7, pela borda HTTP."""
    token = auth_service.authenticate_user(db, email=user.email, password=SENHA)
    assert _me(client, token).status_code == 200

    reset = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=reset, new_password=SENHA_NOVA)

    assert _me(client, token).status_code == 401


def test_troca_de_senha_incrementa_a_versao(db, user):
    antes = user.token_version
    reset = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=reset, new_password=SENHA_NOVA)

    db.expire_all()
    assert db.get(User, user.id).token_version == antes + 1


def test_todas_as_sessoes_caem_de_uma_vez(client, db, user):
    """Vários aparelhos logados: nenhum sobrevive à troca de senha."""
    tokens = [
        auth_service.authenticate_user(db, email=user.email, password=SENHA)
        for _ in range(3)
    ]
    assert all(_me(client, t).status_code == 200 for t in tokens)

    reset = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=reset, new_password=SENHA_NOVA)

    assert all(_me(client, t).status_code == 401 for t in tokens)


def test_login_apos_a_troca_gera_token_que_funciona(client, db, user):
    """Derrubar as sessões não pode impedir de entrar de novo."""
    reset = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=reset, new_password=SENHA_NOVA)

    novo = auth_service.authenticate_user(db, email=user.email, password=SENHA_NOVA)
    assert _me(client, novo).status_code == 200


def test_token_sem_a_claim_de_versao_e_recusado(client, db, user):
    """
    Formato emitido antes desta mudança.

    Aceitá-lo assumindo a versão atual reabriria justamente o buraco que a claim
    fecha.
    """
    import jwt
    from datetime import datetime, timedelta, timezone

    antigo = jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    assert _me(client, antigo).status_code == 401


def test_versao_forjada_para_frente_e_recusada(client, user):
    """Não basta ter a claim: ela precisa BATER com o valor do banco."""
    forjado = create_access_token(str(user.id), token_version=user.token_version + 5)
    assert _me(client, forjado).status_code == 401


# ── #2: uso único sob concorrência ────────────────────────────────────

def test_segunda_tentativa_sequencial_e_recusada(db, user):
    """Uso único no caminho simples (sem concorrência)."""
    token = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=token, new_password=SENHA_NOVA)

    with pytest.raises(AuthError) as exc:
        auth_service.reset_password(db, token=token, new_password="terceira-senha-3")
    assert exc.value.status_code == 400


def test_dois_resets_simultaneos_com_o_mesmo_token(db, user):
    """
    Corrida real, verificada antes da correção: sem a trava de linha, os dois
    pedidos liam `used_at IS NULL` antes de qualquer um gravar, os dois passavam
    pela checagem e os dois trocavam a senha.

    Com `SELECT ... FOR UPDATE`, o segundo bloqueia no SELECT e, quando o
    primeiro confirma, relê a linha já carimbada e é recusado.
    """
    from app.core.database import SessionLocal

    token = auth_service.create_reset_token_for_email(db, email=user.email)

    barreira = threading.Barrier(2)
    resultados: list[str] = []
    trava = threading.Lock()

    def tentar(nome: str) -> None:
        sessao = SessionLocal()
        try:
            barreira.wait(timeout=10)
            auth_service.reset_password(
                sessao, token=token, new_password=f"senha-de-{nome}-999"
            )
            with trava:
                resultados.append("sucesso")
        except AuthError:
            with trava:
                resultados.append("recusado")
        finally:
            sessao.close()

    threads = [threading.Thread(target=tentar, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert resultados.count("sucesso") == 1, (
        f"o mesmo token foi aceito {resultados.count('sucesso')} vezes: {resultados}"
    )
    assert resultados.count("recusado") == 1


def test_corrida_incrementa_a_versao_uma_unica_vez(db, user):
    """Duas trocas paralelas aceitas deixariam a versão inconsistente."""
    from app.core.database import SessionLocal

    antes = user.token_version
    token = auth_service.create_reset_token_for_email(db, email=user.email)

    barreira = threading.Barrier(2)

    def tentar(nome: str) -> None:
        sessao = SessionLocal()
        try:
            barreira.wait(timeout=10)
            auth_service.reset_password(
                sessao, token=token, new_password=f"senha-de-{nome}-999"
            )
        except AuthError:
            pass
        finally:
            sessao.close()

    threads = [threading.Thread(target=tentar, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    db.expire_all()
    assert db.get(User, user.id).token_version == antes + 1
