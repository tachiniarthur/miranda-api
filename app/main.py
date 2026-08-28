"""
Ponto de entrada da aplicação FastAPI (Miranda API).

Inicie em desenvolvimento com:
    uvicorn app.main:app --reload --port 8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api.routes import auth, looks, wardrobe
from app.core.config import settings
from app.core.exception_handlers import register_domain_exception_handlers
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.security_headers import SecurityHeadersMiddleware

app = FastAPI(
    title="Miranda API",
    description="Backend local do Miranda: autenticação, guarda-roupa e look do dia.",
    version="1.0.0",
)

# ── Rate limiting ─────────────────────────────────────────────────────
# O limiter precisa estar em `app.state` para que o handler de 429 consiga
# montar os cabeçalhos Retry-After / X-RateLimit-*. Os limites em si são
# declarados por rota (hoje só nas de autenticação); não há limite global.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# ── Erros de domínio ──────────────────────────────────────────────────
# Tradução central de exceção de domínio → HTTP. Antes disto cada rota repetia
# o próprio `except`, e a rota que esquecesse devolvia 500 cru — falha por
# omissão, que não aparece em code review.
register_domain_exception_handlers(app)

# ── Headers de segurança ──────────────────────────────────────────────
# nosniff + X-Frame-Options em todas as respostas; HSTS só quando a API estiver
# atrás de HTTPS (SECURITY_HSTS_ENABLED).
app.add_middleware(SecurityHeadersMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────
# Permite que o frontend Next.js (localhost:3000) consuma a API em dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    # A combinação "*" + credenciais é barrada na própria configuração
    # (ver o guard em app/core/config.py), então não há como chegar aqui
    # com o wildcard permissivo por engano.
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Storage das imagens das peças ─────────────────────────────────────
# Garante que a pasta exista já no boot (evita erro no primeiro upload).
#
# O mount estático `/static/clothing_images` foi REMOVIDO de propósito: ele
# servia qualquer imagem a qualquer pessoa que tivesse a URL, sem autenticação.
# As imagens agora saem por GET /api/wardrobe/items/{id}/image, que confere a
# posse da peça antes de devolver o arquivo.
Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)

# ── Rotas ─────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api")
app.include_router(wardrobe.router, prefix="/api")
app.include_router(looks.router, prefix="/api")


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    """Verificação simples de que a API está no ar."""
    return {"status": "ok"}
