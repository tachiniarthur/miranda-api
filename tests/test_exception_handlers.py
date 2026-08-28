"""
Handlers centrais de exceção de domínio (achado A5).

A tradução domínio → HTTP vivia copiada em 11 blocos `except` nas rotas. O
problema não era a repetição em si: era a falha por OMISSÃO. Uma rota nova que
esquecesse o bloco devolvia HTTP 500 cru, e esquecimento não aparece em code
review.

Estes testes fixam o contrato de cada handler. Eles existem para garantir que a
centralização não mudou NENHUM status nem NENHUMA mensagem já observável pelo
cliente — o valor de A5 está em remover repetição, não em mudar a API.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.exception_handlers import register_domain_exception_handlers
from app.services.auth_service import AuthError
from app.services.image_validation import ImageValidationError
from app.services.storage import StorageError
from app.services.wardrobe_service import (
    DuplicateImageError,
    QuotaExceededError,
    WardrobeError,
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    """
    App mínimo só com os handlers, sem banco nem rotas reais.

    Testar contra o app de produção acoplaria estes testes às rotas; o que se
    quer verificar aqui é só a tradução exceção → resposta.
    """
    app = FastAPI()
    register_domain_exception_handlers(app)

    @app.get("/auth-error")
    def _auth_error() -> None:
        raise AuthError(401, "E-mail ou senha inválidos.")

    @app.get("/wardrobe-error")
    def _wardrobe_error() -> None:
        raise WardrobeError(404, "Peça não encontrada.")

    @app.get("/duplicate")
    def _duplicate() -> None:
        raise DuplicateImageError()

    @app.get("/quota")
    def _quota() -> None:
        raise QuotaExceededError(150)

    @app.get("/storage")
    def _storage() -> None:
        raise StorageError("Imagem muito grande (máximo 8 MB).")

    @app.get("/image-validation")
    def _image_validation() -> None:
        raise ImageValidationError("Formato não suportado.", status_code=415)

    return TestClient(app)


def test_auth_error_preserva_status_e_mensagem(client):
    r = client.get("/auth-error")
    assert r.status_code == 401
    assert r.json()["detail"] == "E-mail ou senha inválidos."


def test_wardrobe_error_preserva_status_e_mensagem(client):
    r = client.get("/wardrobe-error")
    assert r.status_code == 404
    assert r.json()["detail"] == "Peça não encontrada."


def test_imagem_duplicada_vira_409(client):
    # 409 e não 403: é conflito com o estado da conta, não falta de permissão.
    # O número mora no handler porque a exceção não carrega status.
    r = client.get("/duplicate")
    assert r.status_code == 409
    assert "já está cadastrada" in r.json()["detail"]


def test_quota_estourada_vira_409(client):
    # Mesmo raciocínio do duplicado: o caminho para resolver é apagar uma peça,
    # não pedir acesso.
    r = client.get("/quota")
    assert r.status_code == 409
    assert "150" in r.json()["detail"]


def test_storage_error_vira_400(client):
    # StorageError não carrega status nem `message`: o handler fixa 400 e usa
    # str(exc), exatamente como as rotas faziam.
    r = client.get("/storage")
    assert r.status_code == 400
    assert r.json()["detail"] == "Imagem muito grande (máximo 8 MB)."


def test_image_validation_preserva_o_status_do_motivo(client):
    # 413 tamanho, 415 formato, 400 o resto. Só chega aqui pela rota /analyze:
    # em create_item a exceção já vira StorageError dentro do serviço, e é isso
    # que achata 413/415 em 400 na criação e na edição (achado M14, fora do
    # escopo desta rodada).
    r = client.get("/image-validation")
    assert r.status_code == 415
    assert r.json()["detail"] == "Formato não suportado."


def test_duplicate_e_quota_nao_sao_wardrobe_error():
    """
    Alarme de regressão para a hierarquia.

    `DuplicateImageError` e `QuotaExceededError` herdam de `Exception` direto e
    não carregam `status_code` — por isso cada uma precisa do próprio handler
    com o 409 fixo. Se um dia passarem a herdar de `WardrobeError`, o handler
    daquela classe passaria a atendê-las e o 409 viraria o `status_code` de quem
    levantou. Este teste é o que avisa.
    """
    assert not issubclass(DuplicateImageError, WardrobeError)
    assert not issubclass(QuotaExceededError, WardrobeError)
