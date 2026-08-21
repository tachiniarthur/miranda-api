"""Versão de sessão no usuário (invalida tokens ao trocar a senha)

Acrescenta `users.token_version`. O valor vigente vai como claim `ver` em todo
JWT de acesso e é conferido a cada requisição autenticada; trocar a senha
incrementa a coluna e, com isso, recusa de uma vez todos os tokens já emitidos.

Sem essa coluna, um token roubado seguia válido por até
ACCESS_TOKEN_EXPIRE_MINUTES depois da redefinição de senha — o atacante
continuava dentro justamente no cenário em que a vítima estava reagindo à
invasão.

`server_default="0"` cobre as linhas existentes: todo mundo começa na versão 0,
e os tokens atuais (que não têm a claim) são recusados de qualquer forma, por
não trazerem `ver`.

Revision ID: 0004_user_token_version
Revises: 0003_password_reset_tokens
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_user_token_version"
down_revision: Union[str, None] = "0003_password_reset_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
