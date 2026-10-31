"""bsq: invoices that name no job go to the customer's own job

Revision ID: ca02c3d4e5f6
Revises: ca01b2c3d4e5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca02c3d4e5f6"
down_revision: Union[str, None] = "ca01b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = 'invoice_customer_job'")
    ).first()
    if row is None:
        bind.execute(
            sa.text("INSERT INTO settings (key, value) VALUES ('invoice_customer_job', 'true')")
        )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM settings WHERE key = 'invoice_customer_job'")
    )
