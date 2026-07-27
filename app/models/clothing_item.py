"""Modelo ORM da tabela `clothing_items`."""

import uuid

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ClothingCategory, Formalidade, PesoTermico


# Por padrão, o SQLAlchemy grava o *nome* do membro do Enum (ex.: "BLAZER").
# Queremos gravar o *valor* (ex.: "blazer"), que é o que existe no tipo ENUM do
# Postgres. `values_callable` faz o SQLAlchemy usar os .value dos membros.
def _enum_values(enum_cls) -> list[str]:
    return [member.value for member in enum_cls]


class ClothingItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clothing_items"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[ClothingCategory] = mapped_column(
        SAEnum(
            ClothingCategory,
            name="clothing_category",
            values_callable=_enum_values,
        ),
        nullable=False,
        index=True,
    )
    # Caminho relativo do arquivo salvo localmente (ex.: "clothing_images/<uuid>.png").
    image_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # ── Atributos de moda ─────────────────────────────────────────────
    # Todos aceitam NULL: hoje são preenchidos manualmente no formulário,
    # futuramente serão inferidos pela camada de IA (services/ai).
    cor_primaria: Mapped[str | None] = mapped_column(String(60), nullable=True)
    cor_secundaria: Mapped[str | None] = mapped_column(String(60), nullable=True)
    estampa: Mapped[str | None] = mapped_column(String(80), nullable=True)
    formalidade: Mapped[Formalidade | None] = mapped_column(
        SAEnum(Formalidade, name="formalidade", values_callable=_enum_values),
        nullable=True,
    )
    peso_termico: Mapped[PesoTermico | None] = mapped_column(
        SAEnum(PesoTermico, name="peso_termico", values_callable=_enum_values),
        nullable=True,
    )
    serve_chuva: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Valores possíveis: verao, meia_estacao, inverno.
    estacoes: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    user: Mapped["User"] = relationship(back_populates="clothing_items")  # noqa: F821
