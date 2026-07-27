"""
Configuração do SQLAlchemy: engine, sessão e Base declarativa.
"""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from app.core.config import settings

# `pool_pre_ping` evita erros de conexão morta após o banco reiniciar
# (útil num ambiente local que é ligado/desligado com frequência).
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """Base declarativa compartilhada por todos os modelos ORM."""


def get_db() -> Generator[Session, None, None]:
    """Dependency do FastAPI que fornece uma sessão de banco por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
