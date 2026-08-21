"""
Modelo ORM da tabela `password_reset_tokens`.

O token de redefinição de senha é um valor opaco e aleatório (não um JWT):
guardamos apenas o seu SHA-256, nunca o valor em claro. Isso torna o fluxo
verdadeiramente de USO ÚNICO e REVOGÁVEL — duas propriedades que um JWT
stateless não consegue oferecer:

  - uso único:  `used_at` é carimbado na primeira redefinição bem-sucedida;
  - revogável:  emitir um novo token (ou concluir uma redefinição) marca como
                usados todos os tokens pendentes do mesmo usuário;
  - vazamento do banco não entrega tokens utilizáveis, porque só o hash é
    persistido.

SHA-256 (e não bcrypt) é suficiente aqui: o token tem 256 bits de entropia
vinda de `secrets.token_urlsafe`, então não há espaço de busca a proteger —
diferente de uma senha escolhida por humano.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class PasswordResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 em hexadecimal (64 caracteres) do token entregue ao usuário.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Nulo enquanto o token estiver pendente; carimbado no momento em que o
    # token é consumido OU revogado. Um token com `used_at` preenchido nunca
    # mais é aceito.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="password_reset_tokens"
    )
