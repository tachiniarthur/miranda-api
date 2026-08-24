"""
Modelo ORM da tabela `email_verification_tokens`.

Desenho copiado de propósito do `PasswordResetToken`: valor opaco e aleatório
(não um JWT), do qual só o SHA-256 é persistido. As três propriedades que
motivaram aquele desenho valem igual aqui:

  - uso único:  `used_at` é carimbado na primeira confirmação;
  - revogável:  emitir um token novo marca como usados os pendentes do usuário;
  - vazamento do banco não entrega tokens utilizáveis.

Ter dois desenhos diferentes para o mesmo problema no mesmo projeto seria pior
que a semelhança entre os dois arquivos.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class EmailVerificationToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SHA-256 em hexadecimal (64 caracteres) do token entregue por e-mail.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Nulo enquanto pendente; carimbado ao ser consumido OU revogado.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="email_verification_tokens"
    )
