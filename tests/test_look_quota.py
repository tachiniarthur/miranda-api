"""
Teto da rota que gasta dinheiro (achado C1).

`POST /api/looks/generate` é a única rota paga do sistema: cada chamada custa
até ANTHROPIC_MAX_ATTEMPTS requisições ao claude-opus-5, com o guarda-roupa
inteiro no prompt. Sem teto, uma conta em laço queima o orçamento do dono do
projeto — e, como o endpoint é síncrono e leva até 180s no pior caso, chamadas
concorrentes ainda esgotam o threadpool e derrubam as rotas síncronas.

São duas camadas, de propósito:
  - o rate limit do slowapi é a barreira barata, que rejeita antes de tocar o
    banco — mas vive no Redis, e o Redis pode cair para `memory://`;
  - a contagem em `looks_history` é o teto real, que não depende de Redis
    nenhum e sobrevive a reinício.

Estes testes cobrem a segunda. A rota também não tinha NENHUM teste HTTP: nem
sequer estava verificado que ela exige autenticação.

Roda contra o Postgres de DATABASE_URL; sem banco, é pulado.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.security import create_access_token
from app.main import app
from app.models.look_history import LookHistory
from app.models.user import User

PAYLOAD = {
    "temperatura_min": 18,
    "temperatura_max": 26,
    "condicoes_climaticas": ["sol"],
    "ocasiao": "dia_a_dia",
}


@pytest.fixture(scope="module")
def client():
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres indisponível — teste pulado ({type(exc).__name__}).")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def conta():
    db = SessionLocal()
    user = User(
        name="Quota Look",
        email=f"quota-look-{uuid.uuid4().hex[:8]}@exemplo.com",
        hashed_password="x" * 60,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    yield user
    db.query(LookHistory).filter(LookHistory.user_id == user.id).delete()
    db.query(User).filter(User.id == user.id).delete()
    db.commit()
    db.close()


def _auth(user: User) -> dict[str, str]:
    token = create_access_token(str(user.id), token_version=user.token_version)
    return {"Authorization": f"Bearer {token}"}


def _registrar_geracoes(user: User, quantas: int, *, quando: datetime) -> None:
    """Grava N linhas em looks_history como se a conta já tivesse gerado."""
    db = SessionLocal()
    for _ in range(quantas):
        db.add(LookHistory(user_id=user.id, data_gerado=quando))
    db.commit()
    db.close()


def test_sem_token_a_rota_recusa(client):
    # A rota nunca teve teste HTTP: não estava verificado nem que ela exige
    # autenticação.
    r = client.post("/api/looks/generate", json=PAYLOAD)
    assert r.status_code == 401


def test_guarda_roupa_vazio_degrada_com_200(client, conta):
    # Contrato do serviço: falta de peças não é erro, é resposta com looks
    # vazios e uma nota. Confirma que a quota não transformou isso em 4xx — e,
    # de quebra, que o corpo JSON continua sendo lido como corpo depois de a
    # rota ganhar o decorator do slowapi (a armadilha do functools.wraps).
    r = client.post("/api/looks/generate", json=PAYLOAD, headers=_auth(conta))
    assert r.status_code == 200
    body = r.json()
    assert body["looks"] == []
    assert body["note"]


def test_estourar_a_quota_diaria_devolve_429(client, conta):
    # 429 e não 409: o limite é de tempo, não de estado da conta. Quem estourou
    # resolve esperando o dia virar, não apagando nada.
    _registrar_geracoes(
        conta, settings.MAX_LOOKS_PER_DAY, quando=datetime.now(timezone.utc)
    )

    r = client.post("/api/looks/generate", json=PAYLOAD, headers=_auth(conta))
    assert r.status_code == 429
    assert str(settings.MAX_LOOKS_PER_DAY) in r.json()["detail"]


def test_geracao_de_ontem_nao_conta_para_hoje(client, conta):
    # A janela é o dia corrente em UTC. Sem o filtro de data a quota viraria um
    # teto vitalício, e a conta ficaria travada para sempre.
    ontem = datetime.now(timezone.utc) - timedelta(days=1)
    _registrar_geracoes(conta, settings.MAX_LOOKS_PER_DAY, quando=ontem)

    r = client.post("/api/looks/generate", json=PAYLOAD, headers=_auth(conta))
    assert r.status_code == 200


def test_a_quota_de_uma_conta_nao_afeta_a_outra(client, conta):
    # O eixo da quota é o usuário. Se fosse global, uma conta em laço bloquearia
    # todo mundo — que é justamente o ataque que o teto existe para conter.
    db = SessionLocal()
    outra = User(
        name="Outra",
        email=f"outra-{uuid.uuid4().hex[:8]}@exemplo.com",
        hashed_password="x" * 60,
    )
    db.add(outra)
    db.commit()
    db.refresh(outra)
    outra_id = outra.id
    db.close()

    _registrar_geracoes(
        outra, settings.MAX_LOOKS_PER_DAY, quando=datetime.now(timezone.utc)
    )

    r = client.post("/api/looks/generate", json=PAYLOAD, headers=_auth(conta))
    assert r.status_code == 200

    db = SessionLocal()
    db.query(LookHistory).filter(LookHistory.user_id == outra_id).delete()
    db.query(User).filter(User.id == outra_id).delete()
    db.commit()
    db.close()
