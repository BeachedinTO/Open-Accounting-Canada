"""bsq: Canadian payroll columns on employees, pay stubs and pay runs

Revision ID: ca04e5f6a7b8
Revises: ca03d4e5f6a7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ca04e5f6a7b8"
down_revision: Union[str, None] = "ca03d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMPLOYEE = [
    sa.Column("sin_enc", sa.Text(), nullable=True),
    sa.Column("birth_date", sa.Date(), nullable=True),
    sa.Column("td1_federal", sa.Numeric(15, 2), nullable=True),
    sa.Column("td1_ontario", sa.Numeric(15, 2), nullable=True),
    sa.Column("default_hours", sa.Numeric(10, 2), nullable=True),
    sa.Column("vacation_pct", sa.Numeric(6, 3), nullable=False, server_default="0"),
    sa.Column("pay_method", sa.String(20), nullable=False, server_default="cheque"),
    sa.Column("bank_institution", sa.String(5), nullable=True),
    sa.Column("bank_transit", sa.String(10), nullable=True),
    sa.Column("bank_account_enc", sa.Text(), nullable=True),
    sa.Column("cpp_exempt", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("ei_exempt", sa.Boolean(), nullable=False, server_default="0"),
    sa.Column("end_date", sa.Date(), nullable=True),
]
STUB = [
    sa.Column(n, sa.Numeric(15, 2), nullable=False, server_default="0")
    for n in ("cpp", "cpp2", "ei", "employer_cpp", "employer_cpp2", "employer_ei", "vacation_pay", "wsib")
] + [sa.Column("rate", sa.Numeric(15, 2), nullable=True)]


def upgrade() -> None:
    with op.batch_alter_table("employees") as b:
        for c in EMPLOYEE:
            b.add_column(c)
    with op.batch_alter_table("pay_stubs") as b:
        for c in STUB:
            b.add_column(c)
    with op.batch_alter_table("pay_runs") as b:
        b.add_column(sa.Column("region", sa.String(2), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pay_runs") as b:
        b.drop_column("region")
    with op.batch_alter_table("pay_stubs") as b:
        for c in STUB:
            b.drop_column(c.name)
    with op.batch_alter_table("employees") as b:
        for c in EMPLOYEE:
            b.drop_column(c.name)
