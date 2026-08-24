"""Modelo ORM da tabela `users`."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # Versão de sessão. Incrementada a cada troca de senha; o valor vigente é
    # gravado como claim em todo JWT emitido e conferido a cada requisição
    # autenticada. Um token com versão antiga é recusado, o que derruba TODAS as
    # sessões abertas no instante em que a senha muda.
    #
    # Sem isso, o reset de senha ficava pela metade: quem tivesse roubado um
    # token continuaria dentro por até o prazo de expiração, mesmo depois de a
    # vítima trocar a senha — anulando boa parte do propósito do reset.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )

    # Momento em que o dono do endereço confirmou que ele é dele. Nulo = não
    # verificado.
    #
    # É um TIMESTAMP e não um booleano de propósito: "quando" responde perguntas
    # que "se" não responde — há quanto tempo a conta está verificada, quantas
    # verificaram depois de tal mudança. Um booleano jogaria fora essa
    # informação para economizar 7 bytes.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    clothing_items: Mapped[list["ClothingItem"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    looks: Mapped[list["LookHistory"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    email_verification_tokens: Mapped[list["EmailVerificationToken"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_email_verified(self) -> bool:
        """Açúcar de leitura. A fonte da verdade é `email_verified_at`."""
        return self.email_verified_at is not None
