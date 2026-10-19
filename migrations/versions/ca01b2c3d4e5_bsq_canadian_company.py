"""bsq: a company file becomes a Canadian (Ontario, HST) company

Run once on every company file, new or existing:

- 2200 "Sales Tax Payable" is renamed "HST Payable" (a name someone has
  typed over is left as it is).
- 1350 "HST Recoverable (ITCs)" is added if it isn't there.
- Settings: Tax on Purchases = recoverable (HST input tax credits), the
  default tax rate 13% where none was set, and the home currency CAD for
  a company with no transactions yet (changing the currency under books
  that already hold entries would misstate them, so that case is left
  for the user to decide).

Downgrade puts the account name back and leaves the rest.

Revision ID: ca01b2c3d4e5
Revises: b1c2d3e4f5a6
Create Date: 2026-10-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "ca01b2c3d4e5"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _setting(bind, key):
    row = bind.execute(
        sa.text("SELECT value FROM settings WHERE key = :k"), {"k": key}
    ).fetchone()
    return None if row is None else (row.value or "")


def _put(bind, key, value):
    if _setting(bind, key) is None:
        bind.execute(
            sa.text("INSERT INTO settings (key, value) VALUES (:k, :v)"),
            {"k": key, "v": value},
        )
    else:
        bind.execute(
            sa.text("UPDATE settings SET value = :v WHERE key = :k"),
            {"k": key, "v": value},
        )


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE accounts SET name = 'HST Payable' "
            "WHERE account_number = '2200' AND name = 'Sales Tax Payable'"
        )
    )
    has_accounts = bind.execute(sa.text("SELECT COUNT(*) FROM accounts")).scalar()
    has_itc = bind.execute(
        sa.text("SELECT COUNT(*) FROM accounts WHERE account_number = '1350'")
    ).scalar()
    # A brand-new file is migrated before its chart is seeded; the seed
    # brings 1350 with it then.
    if has_accounts and not has_itc:
        bind.execute(
            sa.text(
                "INSERT INTO accounts (name, account_number, account_type, "
                "is_active, is_system, balance) "
                "VALUES ('HST Recoverable (ITCs)', '1350', 'ASSET', :t, :t, 0)"
            ),
            {"t": True},
        )

    _put(bind, "purchase_tax_recoverable", "true")
    rate = _setting(bind, "default_tax_rate")
    if rate is None or rate.strip() in ("", "0", "0.0", "0.00"):
        _put(bind, "default_tax_rate", "13")
    has_txns = bind.execute(sa.text("SELECT COUNT(*) FROM transactions")).scalar()
    if not has_txns:
        _put(bind, "home_currency", "CAD")


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE accounts SET name = 'Sales Tax Payable' "
            "WHERE account_number = '2200' AND name = 'HST Payable'"
        )
    )
