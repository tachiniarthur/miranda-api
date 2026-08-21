"""Tabela password_reset_tokens (reset de senha de uso único e revogável)

Substitui o token de redefinição stateless (JWT) por um token opaco persistido.
O JWT não conseguia ser de uso único nem revogado: valia até expirar, podia ser
reapresentado várias vezes e continuava válido depois da troca de senha.

Guardamos apenas o SHA-256 do token (`token_hash`), nunca o valor em claro, de
modo que um vazamento do banco não entregue tokens utilizáveis.

`used_at` nulo = token pendente. É carimbado tanto no consumo quanto na
revogação, então a checagem de validade é uma só: `used_at IS NULL AND
expires_at > now()`.

Não há dados a migrar: os tokens antigos eram JWTs que não existiam em tabela
alguma. Eles deixam de ser aceitos assim que esta migration sobe — que é
justamente o efeito desejado.

Revision ID: 0003_password_reset_tokens
Revises: 0002_look_ocasiao
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_password_reset_tokens"
down_revision: Union[str, None] = "0002_look_ocasiao"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_password_reset_tokens_token_hash",
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_password_reset_tokens_user_id", table_name="password_reset_tokens"
    )
    op.drop_index(
        "ix_password_reset_tokens_token_hash", table_name="password_reset_tokens"
    )
    op.drop_table("password_reset_tokens")
