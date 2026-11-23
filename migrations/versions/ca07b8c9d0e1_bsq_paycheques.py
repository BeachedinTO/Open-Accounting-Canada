"""bsq: a paycheque per employee (cheque, e-transfer or direct deposit)

Each employee's pay posts as its own bank entry, as QuickBooks' paycheques:
payee the employee, reference the cheque number or "E-transfer". The stub
keeps the entry, how it was paid and the reference.

Revision ID: ca07b8c9d0e1
Revises: ca06a7b8c9d0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca07b8c9d0e1"
down_revision: Union[str, None] = "ca06a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pay_stubs") as b:
        b.add_column(sa.Column("transaction_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("payment_method", sa.String(20), nullable=True))
        b.add_column(sa.Column("payment_ref", sa.String(40), nullable=True))
        b.create_foreign_key("fk_pay_stubs_transaction_id", "transactions", ["transaction_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("pay_stubs") as b:
        b.drop_constraint("fk_pay_stubs_transaction_id", type_="foreignkey")
        b.drop_column("payment_ref")
        b.drop_column("payment_method")
        b.drop_column("transaction_id")
