"""
Ponto de entrada da aplicação FastAPI (Miranda API).

Inicie em desenvolvimento com:
    uvicorn app.main:app --reload --port 8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, looks, wardrobe
from app.core.config import settings

app = FastAPI(
    title="Miranda API",
    description="Backend local do Miranda: autenticação, guarda-roupa e look do dia.",
    version="1.0.0",
)

# ── CORS ──────────────────────────────────────────────────────────────
# Permite que o frontend Next.js (localhost:3000) consuma a API em dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Arquivos estáticos (imagens das peças) ────────────────────────────
# Garante que a pasta exista antes de montar (evita erro no primeiro boot).
Path(settings.STORAGE_DIR).mkdir(parents=True, exist_ok=True)
app.mount(
    settings.STORAGE_URL_PREFIX,
    StaticFiles(directory=settings.STORAGE_DIR),
    name="clothing_images",
)

# ── Rotas ─────────────────────────────────────────────────────────────
app.include_router(auth.router, prefix="/api")
app.include_router(wardrobe.router, prefix="/api")
app.include_router(looks.router, prefix="/api")


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    """Verificação simples de que a API está no ar."""
    return {"status": "ok"}
