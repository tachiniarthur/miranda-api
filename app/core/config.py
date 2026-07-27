"""
Configuração central da aplicação.

Todas as variáveis sensíveis (string de conexão do banco, chave secreta do JWT,
etc.) são lidas de variáveis de ambiente / do arquivo `.env` na raiz de
`miranda-api`. Nunca coloque segredos reais diretamente no código — use o
arquivo `.env` (que está no .gitignore) baseado no `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Raiz do projeto backend (…/miranda-api). Usada para resolver caminhos
# relativos como a pasta de storage local, independente de onde o processo
# for iniciado.
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    # ── Banco de dados ────────────────────────────────────────────────
    # Ex.: postgresql+psycopg2://miranda_user:senha@localhost:5432/miranda
    DATABASE_URL: str

    # ── Autenticação / JWT ────────────────────────────────────────────
    # Chave usada para assinar os tokens JWT. DEVE ser longa e aleatória em
    # produção. Gere uma com:  python -c "import secrets; print(secrets.token_urlsafe(48))"
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    # Duração do token de acesso, em minutos (padrão: 7 dias).
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    # Duração do token de redefinição de senha, em minutos.
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Servidor ──────────────────────────────────────────────────────
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ── CORS ──────────────────────────────────────────────────────────
    # Origens permitidas para o frontend (Next.js em desenvolvimento).
    # Aceita uma lista separada por vírgula na variável de ambiente.
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── IA (análise de peça, self-hosted) ─────────────────────────────
    # Limiares de confiança (score softmax) do FashionCLIP, UM POR CAMPO:
    # o rótulo vencedor só preenche o campo se o score for >= o limiar do campo;
    # abaixo disso o campo fica nulo, para o usuário preencher à mão. Os padrões
    # vêm da calibração com fotos reais (scripts/calibrate_fashion_clip.py):
    # categoria e estampa têm distribuição de confiança decisiva (topo perto de
    # 1.0), então toleram um limiar alto; a formalidade é estruturalmente mais
    # difusa (a probabilidade se divide entre casual/smart_casual/social), então
    # um limiar alto a deixaria quase sempre nula — por isso o corte mais baixo.
    FASHION_CLIP_THRESHOLD_CATEGORIA: float = 0.80
    FASHION_CLIP_THRESHOLD_ESTAMPA: float = 0.85
    FASHION_CLIP_THRESHOLD_FORMALIDADE: float = 0.60

    # ── Storage local ─────────────────────────────────────────────────
    # Pasta física onde as imagens das peças são gravadas.
    STORAGE_DIR: str = str(BASE_DIR / "storage" / "clothing_images")
    # Prefixo (rota) sob o qual os arquivos estáticos são servidos.
    STORAGE_URL_PREFIX: str = "/static/clothing_images"
    # URL pública base da própria API — usada para montar URLs absolutas das
    # imagens retornadas ao frontend.
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de configuração (cacheada)."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
