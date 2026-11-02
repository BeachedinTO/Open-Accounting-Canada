"""bsq: HST flag on each bill line (QuickBooks' tax code H)

Revision ID: ca03d4e5f6a7
Revises: ca02c3d4e5f6
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca03d4e5f6a7"
down_revision: Union[str, None] = "ca02c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bill_lines") as batch:
        batch.add_column(
            sa.Column("is_taxable", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("bill_lines") as batch:
        batch.drop_column("is_taxable")
