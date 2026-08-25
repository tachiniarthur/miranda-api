"""Hash perceptual da imagem da peça

Acrescenta `clothing_items.image_hash`: o dHash de 64 bits da imagem, usado para
recusar o reenvio da MESMA foto pelo mesmo usuário — o caminho mais barato de
encher a quota de peças.

Nulo para as peças já cadastradas: recalcular exigiria reabrir cada arquivo do
storage, e uma peça sem hash apenas não participa da checagem.

Revision ID: 0006_clothing_item_image_hash
Revises: 0005_email_verification
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_clothing_item_image_hash"
down_revision: Union[str, None] = "0005_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clothing_items", sa.Column("image_hash", sa.String(length=16), nullable=True)
    )
    # O índice é por (user_id, image_hash) porque a consulta é sempre "esta
    # pessoa já tem esta imagem?" — nunca "alguém já tem".
    op.create_index(
        "ix_clothing_items_user_image_hash",
        "clothing_items",
        ["user_id", "image_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_clothing_items_user_image_hash", table_name="clothing_items")
    op.drop_column("clothing_items", "image_hash")
