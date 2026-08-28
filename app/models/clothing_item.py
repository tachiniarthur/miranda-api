"""Modelo ORM da tabela `clothing_items`."""

import uuid

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Index, String, Text
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

    # O índice de deduplicação é COMPOSTO, `(user_id, image_hash)`, porque a
    # consulta é sempre "esta pessoa já tem esta imagem?" — nunca "alguém tem".
    # Criado pela migration 0006 e confirmado no banco.
    #
    # Declará-lo aqui é o que faz o modelo bater com o schema real. Antes o
    # modelo dizia `index=True` no `image_hash` (um índice de coluna única, que
    # não existe) e omitia este composto (que existe): o próximo
    # `alembic revision --autogenerate` emitiria a criação de um índice
    # redundante e poderia propor derrubar o que a consulta usa.
    __table_args__ = (
        Index("ix_clothing_items_user_image_hash", "user_id", "image_hash"),
    )

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

    # dHash de 64 bits da imagem, em hexadecimal. Usado para recusar o reenvio
    # da MESMA foto pelo mesmo usuário — ver `image_validation.perceptual_hash`.
    #
    # Nulo é permitido: as peças cadastradas antes desta coluna não têm hash, e
    # recalculá-las exigiria reabrir cada arquivo do storage. Peça sem hash
    # simplesmente não participa da checagem de duplicata.
    # SEM `index=True`: isso pediria um índice de coluna única
    # (`ix_clothing_items_image_hash`) que nenhuma migration cria e que não
    # existe no banco. O índice real é o composto declarado em `__table_args__`
    # abaixo — e é ele que a consulta de duplicata usa (achado A6).
    image_hash: Mapped[str | None] = mapped_column(String(16), nullable=True)

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
