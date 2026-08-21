"""Ocasião no histórico de looks

Acrescenta `looks_history.ocasiao`, gravada pela tela de look do dia junto do
clima. Texto e não ENUM de propósito: a lista de ocasiões é um parâmetro de
produto que deve poder crescer sem `ALTER TYPE`, e registros antigos com uma
ocasião descontinuada continuam legíveis.

Nullable porque os registros gerados antes desta feature não têm ocasião — não
há valor correto para retroagir, e inventar um ("dia_a_dia") falsificaria o
histórico.

As condições climáticas múltiplas NÃO exigem migration: continuam na coluna
`condicao_climatica` (String(60)), agora com os valores unidos por ", ".

Revision ID: 0002_look_ocasiao
Revises: 0001_initial
Create Date: 2026-08-11

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_look_ocasiao"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "looks_history",
        sa.Column("ocasiao", sa.String(length=40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("looks_history", "ocasiao")
