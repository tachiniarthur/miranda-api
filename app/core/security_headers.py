"""
Middleware de headers de segurança.

São instruções ao NAVEGADOR sobre como tratar a resposta. Não substituem
nenhuma checagem do servidor — são a camada que limita o estrago quando algo
mais falha.

  - `X-Content-Type-Options: nosniff`
        Proíbe o navegador de adivinhar o tipo do conteúdo a partir dos bytes
        quando ele discorda do Content-Type declarado. Importa especialmente
        porque esta API devolve arquivos enviados por usuários (as imagens das
        peças): sem o header, um arquivo servido como `image/png` cujo conteúdo
        pareça HTML poderia ser interpretado como HTML por navegadores antigos,
        virando XSS na origem da própria API.

  - `X-Frame-Options: DENY`
        Impede que qualquer site coloque respostas desta API dentro de um
        iframe, fechando clickjacking.

  - `Strict-Transport-Security`
        Obriga o navegador a só falar HTTPS com este host pelo tempo indicado.
        Só faz sentido quando a API já está atrás de HTTPS — daí ser
        configurável e vir desligado (ver `SECURITY_HSTS_ENABLED` em
        app/core/config.py).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Acrescenta os headers de segurança a todas as respostas da aplicação."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        hsts_enabled: bool | None = None,
        hsts_max_age: int | None = None,
    ) -> None:
        super().__init__(app)
        self._hsts_enabled = (
            settings.SECURITY_HSTS_ENABLED if hsts_enabled is None else hsts_enabled
        )
        self._hsts_max_age = (
            settings.SECURITY_HSTS_MAX_AGE if hsts_max_age is None else hsts_max_age
        )

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # `setdefault`: uma rota que precise de um valor próprio (por exemplo,
        # um Content-Type-Options diferente) não é sobrescrita pelo middleware.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")

        if self._hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={self._hsts_max_age}; includeSubDomains",
            )

        return response
