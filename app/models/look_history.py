"""Modelo ORM da tabela `looks_history`.

Schema pronto para quando a geração de looks por IA for implementada. Ainda
não é usado pela interface, mas a migration é aplicada normalmente.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import UUIDPrimaryKeyMixin


class LookHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "looks_history"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    data_gerado: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    temperatura_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperatura_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    condicao_climatica: Mapped[str | None] = mapped_column(String(60), nullable=True)

    # Lista de uuids de clothing_items sugeridos, armazenada como JSONB para
    # flexibilidade (pode conter múltiplos looks, cada um com seus itens).
    itens_sugeridos: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    justificativa: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="looks")  # noqa: F821
