"""
Tradução central de exceção de domínio para resposta HTTP.

Antes isto vivia copiado em 11 blocos `except` espalhados pelas rotas (achado
A5). O problema não era a repetição em si: era a falha por OMISSÃO. Uma rota
nova que esquecesse o bloco devolvia HTTP 500 cru, e esquecimento não aparece em
code review — só em produção.

Aqui a proteção é opt-out: qualquer rota que levante uma exceção de domínio já
recebe o status e a mensagem certos sem escrever uma linha.

⚠️ Os status abaixo reproduzem EXATAMENTE o que as rotas produziam antes. Mudar
qualquer um deles é mudar o contrato observável da API, não refatorar.

Nota sobre `status_code` nas exceções: `AuthError` e `WardrobeError` carregam o
status, o que é HTTP infiltrado no domínio pela porta dos fundos. Tirá-lo
exigiria quebrar as duas em subclasses por status e mudar todo call site —
trabalho próprio, registrado como dívida em ESTADO-DO-PROJETO.md.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.auth_service import AuthError
from app.services.image_validation import ImageValidationError
from app.services.look_service import LookQuotaExceededError
from app.services.storage import StorageError
from app.services.wardrobe_service import (
    DuplicateImageError,
    QuotaExceededError,
    WardrobeError,
)


def _detail(status_code: int, detail: str) -> JSONResponse:
    """Mesma forma de corpo que o `HTTPException` do FastAPI produz."""
    return JSONResponse(status_code=status_code, content={"detail": detail})


async def _handle_auth_error(_: Request, exc: AuthError) -> JSONResponse:
    return _detail(exc.status_code, exc.message)


async def _handle_wardrobe_error(_: Request, exc: WardrobeError) -> JSONResponse:
    return _detail(exc.status_code, exc.message)


async def _handle_duplicate_image(_: Request, exc: DuplicateImageError) -> JSONResponse:
    # 409 pelo mesmo motivo da quota: conflito com o estado da conta, não falta
    # de permissão. A exceção não carrega status — o número mora aqui.
    return _detail(409, str(exc))


async def _handle_quota_exceeded(_: Request, exc: QuotaExceededError) -> JSONResponse:
    # 409 e não 403: não é falta de permissão, é conflito com o estado atual da
    # conta — e o caminho para resolver é apagar uma peça, não pedir acesso.
    return _detail(409, str(exc))


async def _handle_look_quota(_: Request, exc: LookQuotaExceededError) -> JSONResponse:
    # 429 e NÃO 409, ao contrário da quota de peças: aqui o limite é de tempo,
    # não de estado da conta. Quem estourou resolve esperando o dia virar, não
    # apagando nada — e 429 é o status que diz isso.
    return _detail(429, str(exc))


async def _handle_storage_error(_: Request, exc: StorageError) -> JSONResponse:
    # StorageError não carrega status nem `message`; 400 e `str(exc)` é o que as
    # rotas faziam.
    return _detail(400, str(exc))


async def _handle_image_validation(
    _: Request, exc: ImageValidationError
) -> JSONResponse:
    # Preserva o status do motivo: 413 tamanho, 415 formato, 400 o resto.
    #
    # Só chega aqui pela rota /analyze. Em `create_item` a exceção já é
    # convertida em StorageError dentro do serviço, e é isso que achata 413/415
    # em 400 na criação e na edição — o achado M14, que está FORA do escopo
    # desta rodada. Não corrigir aqui de carona: há teste fixando o 400.
    return _detail(exc.status_code, exc.message)


def register_domain_exception_handlers(app: FastAPI) -> None:
    """Registra a tradução domínio → HTTP. Chamado uma vez, no boot."""
    app.add_exception_handler(AuthError, _handle_auth_error)  # type: ignore[arg-type]
    app.add_exception_handler(WardrobeError, _handle_wardrobe_error)  # type: ignore[arg-type]
    app.add_exception_handler(DuplicateImageError, _handle_duplicate_image)  # type: ignore[arg-type]
    app.add_exception_handler(QuotaExceededError, _handle_quota_exceeded)  # type: ignore[arg-type]
    app.add_exception_handler(LookQuotaExceededError, _handle_look_quota)  # type: ignore[arg-type]
    app.add_exception_handler(StorageError, _handle_storage_error)  # type: ignore[arg-type]
    app.add_exception_handler(ImageValidationError, _handle_image_validation)  # type: ignore[arg-type]
