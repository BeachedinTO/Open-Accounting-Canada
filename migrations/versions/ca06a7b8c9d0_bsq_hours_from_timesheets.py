"""bsq: pay hours from timesheets, per employee

QuickBooks' "Use time data to create pay cheques", per employee. Off: the
pay run uses the employee's usual hours each pay (salaried staff paid a
fixed 80 hours, timesheets kept for job tracking). On: the pay run takes
the timesheet hours in the pay period (hourly staff whose hours vary).
Existing employees with no usual hours (hours vary) start on.

Revision ID: ca06a7b8c9d0
Revises: ca05f6a7b8c9
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca06a7b8c9d0"
down_revision: Union[str, None] = "ca05f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("employees") as b:
        b.add_column(
            sa.Column("hours_from_timesheets", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.execute("UPDATE employees SET hours_from_timesheets = 1 WHERE default_hours IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("employees") as b:
        b.drop_column("hours_from_timesheets")
