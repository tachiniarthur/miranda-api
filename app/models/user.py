"""Modelo ORM da tabela `users`."""

from sqlalchemy import Integer, String
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
