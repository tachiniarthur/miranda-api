"""Estrutura inicial: users, clothing_items, looks_history

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Enums de domínio (valores normalizados, minúsculos e sem acento).
clothing_category = postgresql.ENUM(
    "blazer", "vestido", "calca", "camisa", "casaco", "malha",
    "saia", "calcado", "cachecol", "acessorio", "outros",
    name="clothing_category",
)
formalidade = postgresql.ENUM(
    "casual", "smart_casual", "social", "esporte", name="formalidade",
)
peso_termico = postgresql.ENUM(
    "leve", "medio", "pesado", name="peso_termico",
)


def upgrade() -> None:
    bind = op.get_bind()
    clothing_category.create(bind, checkfirst=True)
    formalidade.create(bind, checkfirst=True)
    peso_termico.create(bind, checkfirst=True)

    # ── users ─────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
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
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── clothing_items ────────────────────────────────────────────────
    op.create_table(
        "clothing_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(name="clothing_category", create_type=False),
            nullable=False,
        ),
        sa.Column("image_path", sa.String(length=512), nullable=False),
        sa.Column("cor_primaria", sa.String(length=60), nullable=True),
        sa.Column("cor_secundaria", sa.String(length=60), nullable=True),
        sa.Column("estampa", sa.String(length=80), nullable=True),
        sa.Column(
            "formalidade",
            postgresql.ENUM(name="formalidade", create_type=False),
            nullable=True,
        ),
        sa.Column(
            "peso_termico",
            postgresql.ENUM(name="peso_termico", create_type=False),
            nullable=True,
        ),
        sa.Column("serve_chuva", sa.Boolean(), nullable=True),
        sa.Column("estacoes", postgresql.ARRAY(sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_clothing_items_user_id", "clothing_items", ["user_id"]
    )
    op.create_index(
        "ix_clothing_items_category", "clothing_items", ["category"]
    )

    # ── looks_history ─────────────────────────────────────────────────
    op.create_table(
        "looks_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "data_gerado",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("temperatura_min", sa.Float(), nullable=True),
        sa.Column("temperatura_max", sa.Float(), nullable=True),
        sa.Column("condicao_climatica", sa.String(length=60), nullable=True),
        sa.Column("itens_sugeridos", postgresql.JSONB(), nullable=True),
        sa.Column("justificativa", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_looks_history_user_id", "looks_history", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_looks_history_user_id", table_name="looks_history")
    op.drop_table("looks_history")

    op.drop_index("ix_clothing_items_category", table_name="clothing_items")
    op.drop_index("ix_clothing_items_user_id", table_name="clothing_items")
    op.drop_table("clothing_items")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    peso_termico.drop(bind, checkfirst=True)
    formalidade.drop(bind, checkfirst=True)
    clothing_category.drop(bind, checkfirst=True)
