"""
Ambiente de execução das migrations do Alembic.

A URL do banco e a metadata dos modelos vêm da própria aplicação, para manter
uma única fonte de verdade (settings.DATABASE_URL e app.core.database.Base).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Garante que o pacote `app` seja importável ao rodar `alembic` a partir da
# raiz de miranda-api.
from app.core.config import settings
from app.core.database import Base

# Importa os modelos para que fiquem registrados na metadata do SQLAlchemy
# (necessário para o autogenerate detectar as tabelas).
import app.models  # noqa: F401

config = context.config

# Injeta a URL de conexão vinda do .env.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Executa as migrations em modo 'offline' (gera SQL sem conectar)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Executa as migrations conectando de fato ao banco."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
