"""bsq: weekly timesheet as in QuickBooks - service item, billable, billed

A time entry carries the service item it is billed as, whether it is
billable to its job's customer, and the invoice line that billed it.

Revision ID: ca05f6a7b8c9
Revises: ca04e5f6a7b8
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca05f6a7b8c9"
down_revision: Union[str, None] = "ca04e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("time_entries") as b:
        b.add_column(sa.Column("service_item_id", sa.Integer(), nullable=True))
        b.add_column(
            sa.Column("is_billable", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        b.add_column(sa.Column("billed_invoice_line_id", sa.Integer(), nullable=True))
        b.create_foreign_key(
            "fk_time_entries_service_item_id", "items", ["service_item_id"], ["id"]
        )
        b.create_foreign_key(
            "fk_time_entries_billed_invoice_line_id",
            "invoice_lines",
            ["billed_invoice_line_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("time_entries") as b:
        b.drop_constraint("fk_time_entries_billed_invoice_line_id", type_="foreignkey")
        b.drop_constraint("fk_time_entries_service_item_id", type_="foreignkey")
        b.drop_column("billed_invoice_line_id")
        b.drop_column("is_billable")
        b.drop_column("service_item_id")
