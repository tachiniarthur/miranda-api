"""
Testes da mitigação de timing attack / enumeração de e-mail no login.

Itens #3 e #4 da revisão de segurança.

A versão anterior de `authenticate_user` era:

    if user is None or not verify_password(password, user.hashed_password):

O curto-circuito do `or` fazia com que um e-mail inexistente respondesse SEM
executar o bcrypt. Com custo 12 isso são ~250 ms de diferença — mensurável numa
única requisição, sem precisar de estatística. A mensagem genérica de erro não
escondia nada: bastava cronometrar.

A garantia é verificada de duas formas: contando as chamadas ao bcrypt (rápido e
determinístico) e medindo o tempo de fato (mais próximo do ataque real, com
margem folgada para não ficar instável).
"""

from __future__ import annotations

import time
import uuid

import pytest

from app.core import security
from app.core.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.models.user import User
from app.services import auth_service
from app.services.auth_service import AuthError

SENHA = "senha-real-desta-conta-123"
EMAIL_INEXISTENTE = "nao-existe-em-lugar-nenhum@exemplo.com"


@pytest.fixture(scope="module")
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
        session.close()


@pytest.fixture(scope="module")
def user(db):
    u = User(
        name="Usuária Existente",
        email=f"timing-{uuid.uuid4().hex}@exemplo.com",
        hashed_password=hash_password(SENHA),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    yield u
    db.delete(u)
    db.commit()


# ── O hash dummy em si ────────────────────────────────────────────────

def test_dummy_hash_e_constante_fixa():
    """Pré-computado, não gerado no boot: nada de 250 ms de bcrypt ao subir."""
    assert isinstance(DUMMY_PASSWORD_HASH, str)
    assert DUMMY_PASSWORD_HASH.startswith("$2b$")


def test_dummy_hash_tem_o_mesmo_custo_dos_hashes_reais():
    """Custo diferente = tempo diferente, e o disfarce não funcionaria."""
    custo_dummy = DUMMY_PASSWORD_HASH.split("$")[2]
    custo_real = hash_password("qualquer-senha-123").split("$")[2]
    assert custo_dummy == custo_real == "12"


def test_nenhuma_senha_plausivel_bate_com_o_dummy_hash():
    for tentativa in ("", "senha", SENHA, "123456", "admin"):
        assert not verify_password(tentativa, DUMMY_PASSWORD_HASH)


# ── A garantia central: bcrypt roda nos dois caminhos ─────────────────

@pytest.fixture
def contador_bcrypt(monkeypatch):
    """Conta as chamadas a verify_password vistas pelo auth_service."""
    chamadas: list[str] = []
    original = auth_service.verify_password

    def espiao(plain: str, hashed: str) -> bool:
        chamadas.append(hashed)
        return original(plain, hashed)

    monkeypatch.setattr(auth_service, "verify_password", espiao)
    return chamadas


def test_email_inexistente_ainda_executa_bcrypt(db, contador_bcrypt):
    """O coração do item #3: não pular o cálculo quando o usuário não existe."""
    with pytest.raises(AuthError):
        auth_service.authenticate_user(
            db, email=EMAIL_INEXISTENTE, password="qualquer-senha-123"
        )

    assert len(contador_bcrypt) == 1, "bcrypt não foi executado"
    assert contador_bcrypt[0] == DUMMY_PASSWORD_HASH


def test_senha_errada_executa_bcrypt_contra_o_hash_real(db, user, contador_bcrypt):
    with pytest.raises(AuthError):
        auth_service.authenticate_user(
            db, email=user.email, password="senha-errada-987"
        )

    assert len(contador_bcrypt) == 1
    assert contador_bcrypt[0] == user.hashed_password


def test_os_dois_caminhos_de_falha_executam_o_mesmo_numero_de_bcrypts(
    db, user, contador_bcrypt
):
    with pytest.raises(AuthError):
        auth_service.authenticate_user(db, email=EMAIL_INEXISTENTE, password="x-123456")
    inexistente = len(contador_bcrypt)

    contador_bcrypt.clear()
    with pytest.raises(AuthError):
        auth_service.authenticate_user(db, email=user.email, password="x-123456")
    senha_errada = len(contador_bcrypt)

    assert inexistente == senha_errada == 1


# ── A mensagem continua indistinguível ────────────────────────────────

def test_mensagem_de_erro_e_identica_nos_dois_casos(db, user):
    mensagens = set()
    for email in (EMAIL_INEXISTENTE, user.email):
        with pytest.raises(AuthError) as exc:
            auth_service.authenticate_user(db, email=email, password="errada-123456")
        mensagens.add((exc.value.status_code, exc.value.message))

    assert len(mensagens) == 1, mensagens


def test_credenciais_corretas_continuam_funcionando(db, user):
    """A mitigação não pode ter quebrado o login legítimo."""
    assert auth_service.authenticate_user(db, email=user.email, password=SENHA)


# ── Medição de tempo ──────────────────────────────────────────────────

def _mediana_ms(fn, repeticoes: int = 3) -> float:
    amostras = []
    for _ in range(repeticoes):
        inicio = time.perf_counter()
        try:
            fn()
        except AuthError:
            pass
        amostras.append((time.perf_counter() - inicio) * 1000)
    return sorted(amostras)[len(amostras) // 2]


def test_tempos_de_resposta_sao_da_mesma_ordem(db, user):
    """
    Mede os dois caminhos de falha.

    A margem é folgada de propósito: o que se quer detectar é a REGRESSÃO
    gritante (uma ordem de grandeza, o bcrypt sendo pulado), não variação de
    alguns milissegundos, que dependeria da carga da máquina e deixaria o teste
    instável.
    """
    t_inexistente = _mediana_ms(
        lambda: auth_service.authenticate_user(
            db, email=EMAIL_INEXISTENTE, password="errada-123456"
        )
    )
    t_senha_errada = _mediana_ms(
        lambda: auth_service.authenticate_user(
            db, email=user.email, password="errada-123456"
        )
    )

    maior, menor = max(t_inexistente, t_senha_errada), min(t_inexistente, t_senha_errada)
    assert menor > 10, (
        f"caminho rápido demais ({menor:.1f} ms): o bcrypt provavelmente foi pulado"
    )
    assert maior / menor < 3, (
        f"diferença grande demais: inexistente={t_inexistente:.1f} ms, "
        f"senha errada={t_senha_errada:.1f} ms"
    )
