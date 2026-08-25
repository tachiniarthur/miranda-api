"""
Testes do fluxo de redefinição de senha (uso único e revogável).

Rodam contra o Postgres configurado em DATABASE_URL, porque é justamente a
persistência do token que está sob teste — um stub não provaria nada aqui. Se o
banco não estiver acessível, os testes são PULADOS, no mesmo espírito do
test_analysis_regression quando o FashionCLIP não está disponível.

Cada teste cria o próprio usuário com e-mail aleatório e o remove ao final, sem
tocar em dados existentes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.security import hash_password, hash_reset_token, verify_password
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services import auth_service
from app.services.auth_service import AuthError

SENHA_ORIGINAL = "senha-original-123"
SENHA_NOVA = "senha-nova-456"


@pytest.fixture
def db():
    from app.core.database import SessionLocal, engine

    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def user(db):
    u = User(
        name="Usuária de Teste",
        email=f"teste-reset-{uuid.uuid4().hex}@exemplo.test",
        hashed_password=hash_password(SENHA_ORIGINAL),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    try:
        yield u
    finally:
        # O cascade em users.id remove os tokens junto.
        db.delete(u)
        db.commit()


def _tokens_do(db, user) -> list[PasswordResetToken]:
    return list(
        db.scalars(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        ).all()
    )


# ── Emissão ───────────────────────────────────────────────────────────

def test_token_em_claro_nunca_e_persistido(db, user):
    token = auth_service.create_reset_token_for_email(db, email=user.email)
    assert token

    registros = _tokens_do(db, user)
    assert len(registros) == 1
    assert registros[0].token_hash == hash_reset_token(token)
    assert registros[0].token_hash != token
    assert registros[0].used_at is None


def test_email_inexistente_nao_gera_token(db):
    assert (
        auth_service.create_reset_token_for_email(
            db, email=f"nao-existe-{uuid.uuid4().hex}@exemplo.test"
        )
        is None
    )


def test_emitir_token_novo_revoga_o_anterior(db, user):
    antigo = auth_service.create_reset_token_for_email(db, email=user.email)
    novo = auth_service.create_reset_token_for_email(db, email=user.email)
    assert antigo != novo

    # O antigo já não serve...
    with pytest.raises(AuthError):
        auth_service.reset_password(db, token=antigo, new_password=SENHA_NOVA)
    # ...e o novo serve.
    auth_service.reset_password(db, token=novo, new_password=SENHA_NOVA)


# ── Consumo ───────────────────────────────────────────────────────────

def test_token_valido_troca_a_senha(db, user):
    token = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=token, new_password=SENHA_NOVA)

    db.refresh(user)
    assert verify_password(SENHA_NOVA, user.hashed_password)
    assert not verify_password(SENHA_ORIGINAL, user.hashed_password)


def test_token_e_de_uso_unico(db, user):
    """O cerne do achado: reapresentar o mesmo token não pode funcionar."""
    token = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=token, new_password=SENHA_NOVA)

    with pytest.raises(AuthError) as exc:
        auth_service.reset_password(db, token=token, new_password="terceira-senha-789")
    assert exc.value.status_code == 400

    # A segunda tentativa não pode ter alterado nada.
    db.refresh(user)
    assert verify_password(SENHA_NOVA, user.hashed_password)


def test_consumo_carimba_used_at(db, user):
    token = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=token, new_password=SENHA_NOVA)

    registro = _tokens_do(db, user)[0]
    db.refresh(registro)
    assert registro.used_at is not None


def test_token_expirado_e_recusado(db, user):
    token = auth_service.create_reset_token_for_email(db, email=user.email)

    registro = _tokens_do(db, user)[0]
    registro.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(registro)
    db.commit()

    with pytest.raises(AuthError):
        auth_service.reset_password(db, token=token, new_password=SENHA_NOVA)
    db.refresh(user)
    assert verify_password(SENHA_ORIGINAL, user.hashed_password)


def test_token_inexistente_e_recusado(db, user):
    with pytest.raises(AuthError) as exc:
        auth_service.reset_password(
            db, token="token-que-nunca-foi-emitido", new_password=SENHA_NOVA
        )
    assert exc.value.status_code == 400


def test_mensagem_de_erro_e_a_mesma_para_todos_os_motivos(db, user):
    """Inexistente, expirado e já usado não podem ser distinguíveis."""
    usado = auth_service.create_reset_token_for_email(db, email=user.email)
    auth_service.reset_password(db, token=usado, new_password=SENHA_NOVA)

    expirado = auth_service.create_reset_token_for_email(db, email=user.email)
    registro = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == hash_reset_token(expirado)
        )
    )
    registro.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.add(registro)
    db.commit()

    mensagens = set()
    for token in ("inexistente", usado, expirado):
        with pytest.raises(AuthError) as exc:
            auth_service.reset_password(db, token=token, new_password="outra-senha-999")
        mensagens.add(exc.value.message)

    assert len(mensagens) == 1, mensagens


def test_trocar_a_senha_revoga_os_demais_tokens_pendentes(db, user):
    """
    Nenhum token sobrevive à troca de senha.

    Cenário real: a vítima pede o reset, um atacante também pede (ou o inverso).
    Concluída a redefinição, qualquer token pendente do usuário morre junto.
    """
    primeiro = auth_service.create_reset_token_for_email(db, email=user.email)
    # `create_reset_token_for_email` já revoga o anterior, então inserimos um
    # segundo token pendente à mão para simular dois vivos ao mesmo tempo.
    paralelo = "token-paralelo-em-claro"
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_reset_token(paralelo),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
    )
    db.commit()

    auth_service.reset_password(db, token=primeiro, new_password=SENHA_NOVA)

    with pytest.raises(AuthError):
        auth_service.reset_password(db, token=paralelo, new_password="terceira-999")
    assert all(t.used_at is not None for t in _tokens_do(db, user))


# ── Entrega do token por e-mail (substitui o log em texto puro) ─────────────
# O token deixou de ir para o log do servidor. Estes testes protegem as duas
# metades disso: que ele agora vai por e-mail, e que ele NÃO vai mais para
# lugar nenhum que não seja a caixa do dono do endereço.
#
# O envio é interceptado em `app.api.routes.auth.send_email` — o nome que a
# ROTA enxerga — e não no módulo de origem: a rota importa a função por valor,
# então trocar o atributo em `app.services.email.sender` não a alcançaria.


@pytest.fixture
def usuario_web(db):
    """
    Um usuário com e-mail que passa pela validação da BORDA HTTP.

    O fixture `user` usa o TLD `.test` (reservado para testes, o certo para
    quem fala direto com o serviço), mas o `EmailStr` do pydantic recusa
    domínios de uso especial — e estes testes entram pela rota, não pelo
    serviço. Daí um segundo fixture, em vez de afrouxar o primeiro.
    """
    u = User(
        name="Usuária de Teste",
        email=f"teste-reset-{uuid.uuid4().hex}@exemplo.com",
        hashed_password=hash_password(SENHA_ORIGINAL),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    try:
        yield u
    finally:
        db.delete(u)
        db.commit()


@pytest.fixture
def client(db):
    """
    Cliente HTTP ligado ao MESMO banco do fixture `db`, para que o usuário
    criado pelo fixture `user` exista para a rota.

    `limiter.reset()` nas duas pontas zera a janela do rate limit de auth: sem
    isso, um teste que faz vários pedidos contaminaria os seguintes.
    """
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.core.rate_limit import limiter
    from app.main import app

    def _sessao_do_teste():
        yield db

    app.dependency_overrides[get_db] = _sessao_do_teste
    limiter.reset()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        limiter.reset()


def test_o_token_vai_por_email_e_nao_para_o_log(
    client, usuario_web, caplog, monkeypatch
):
    import logging
    import re

    from app.api.routes import auth as rota_auth

    enviados = []
    monkeypatch.setattr(rota_auth, "send_email", lambda m: enviados.append(m) or True)

    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/auth/forgot-password", json={"email": usuario_web.email}
        )

    assert resp.status_code == 200
    assert len(enviados) == 1, "um e-mail devia ter sido enviado"
    assert enviados[0].to == usuario_web.email

    # O token está no corpo do e-mail...
    achado = re.search(r"token=([A-Za-z0-9_\-]{20,})", enviados[0].text)
    assert achado, "o e-mail precisa carregar o link com o token"

    # ...e NÃO está em lugar nenhum do log.
    assert achado.group(1) not in caplog.text


def test_email_desconhecido_nao_dispara_envio(client, monkeypatch):
    from app.api.routes import auth as rota_auth

    enviados = []
    monkeypatch.setattr(rota_auth, "send_email", lambda m: enviados.append(m) or True)

    resp = client.post(
        "/api/auth/forgot-password", json={"email": "ninguem-aqui@exemplo.com"}
    )

    assert resp.status_code == 200
    assert enviados == [], "não existe conta: não há para quem enviar"


def test_a_resposta_e_identica_exista_ou_nao_o_email(client, usuario_web, monkeypatch):
    """
    A resposta não pode variar com a existência da conta NEM com o sucesso do
    envio — as duas coisas seriam canais de enumeração.
    """
    from app.api.routes import auth as rota_auth

    # Simula servidor de e-mail fora do ar.
    monkeypatch.setattr(rota_auth, "send_email", lambda m: False)

    existe = client.post(
        "/api/auth/forgot-password", json={"email": usuario_web.email}
    )
    nao_existe = client.post(
        "/api/auth/forgot-password", json={"email": "ninguem@exemplo.com"}
    )

    assert existe.status_code == nao_existe.status_code == 200
    assert existe.json() == nao_existe.json()


def test_falha_de_entrega_nao_quebra_o_pedido(client, usuario_web, monkeypatch):
    """Servidor de e-mail fora do ar continua devolvendo 200, não 500."""
    from app.api.routes import auth as rota_auth

    def _explode(m):
        raise RuntimeError("smtp morreu de um jeito imprevisto")

    monkeypatch.setattr(rota_auth, "send_email", _explode)

    resp = client.post(
        "/api/auth/forgot-password", json={"email": usuario_web.email}
    )

    assert resp.status_code == 200
