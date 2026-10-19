"""bsq (Canada): tax on purchases as HST input tax credits.

With the setting `purchase_tax_recoverable` = "true", the tax on a bill,
on a purchase order turned into a bill, and on a vendor credit posts to
1350 HST Recoverable (ITCs) instead of being spread into the cost of the
lines. 2200 (tax collected on sales) is never touched by a purchase. With
the setting off, the original behaviour is unchanged.
"""

from decimal import Decimal

from app.models.accounts import Account
from app.models.bills import Bill
from app.models.transactions import TransactionLine

HST = 0.13


def _gl(db, account_id):
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum(
        (Decimal(str(r.debit or 0)) - Decimal(str(r.credit or 0)) for r in rows),
        Decimal(0),
    )


def _itc_id(db):
    acct = db.query(Account).filter_by(account_number="1350").first()
    return acct.id if acct else None


def _recoverable(client, on=True):
    r = client.put("/api/settings", json={"purchase_tax_recoverable": "true" if on else "false"})
    assert r.status_code == 200, r.text


def _vendor(client, name="Ontario Stone Supply"):
    r = client.post("/api/vendors", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, seed_accounts, day="2026-11-03"):
    r = client.post(
        "/api/bills",
        json={
            "vendor_id": vendor_id,
            "date": day,
            "tax_rate": HST,
            "lines": [
                {"description": "armour stone", "quantity": 1, "rate": 1000,
                 "account_id": seed_accounts["5100"].id},
                {"description": "delivery", "quantity": 1, "rate": 250,
                 "account_id": seed_accounts["6800"].id},
            ],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_off_by_default_tax_stays_in_the_cost(client, db_session, seed_accounts):
    v = _vendor(client)
    b = _bill(client, v["id"], seed_accounts)
    assert Decimal(b["total"]) == Decimal("1412.50")
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("1130.00")
    assert _itc_id(db_session) is None


def test_a_bill_posts_its_hst_to_the_itc_account(client, db_session, seed_accounts):
    _recoverable(client)
    v = _vendor(client)
    b = _bill(client, v["id"], seed_accounts)
    assert Decimal(b["tax_amount"]) == Decimal("162.50")
    assert Decimal(b["total"]) == Decimal("1412.50")
    # Lines at their pre-tax amounts, the HST on 1350, A/P owes the total.
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("1000.00")
    assert _gl(db_session, seed_accounts["6800"].id) == Decimal("250.00")
    assert _gl(db_session, _itc_id(db_session)) == Decimal("162.50")
    assert -_gl(db_session, seed_accounts["2000"].id) == Decimal("1412.50")
    # Tax collected on sales is untouched.
    assert _gl(db_session, seed_accounts["2200"].id) == 0


def test_voiding_the_bill_takes_the_itc_back(client, db_session, seed_accounts):
    _recoverable(client)
    v = _vendor(client)
    b = _bill(client, v["id"], seed_accounts)
    assert client.post(f"/api/bills/{b['id']}/void").status_code == 200
    assert _gl(db_session, _itc_id(db_session)) == 0
    assert _gl(db_session, seed_accounts["2000"].id) == 0


def test_editing_the_bill_reposts_the_itc(client, db_session, seed_accounts):
    _recoverable(client)
    v = _vendor(client)
    b = _bill(client, v["id"], seed_accounts)
    r = client.put(
        f"/api/bills/{b['id']}",
        json={
            "lines": [
                {"description": "armour stone", "quantity": 2, "rate": 1000,
                 "account_id": seed_accounts["5100"].id},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert _gl(db_session, _itc_id(db_session)) == Decimal("260.00")
    assert -_gl(db_session, seed_accounts["2000"].id) == Decimal("2260.00")


def test_a_vendor_credit_reverses_its_itc(client, db_session, seed_accounts):
    _recoverable(client)
    v = _vendor(client)
    _bill(client, v["id"], seed_accounts)
    r = client.post(
        "/api/vendor-credits",
        json={
            "vendor_id": v["id"],
            "date": "2026-11-10",
            "tax_rate": HST,
            "lines": [{"description": "returned stone", "quantity": 1, "rate": 1000,
                       "account_id": seed_accounts["5100"].id, "line_order": 0}],
        },
    )
    assert r.status_code == 201, r.text
    assert _gl(db_session, _itc_id(db_session)) == Decimal("32.50")  # 162.50 - 130
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("0.00")
    assert _gl(db_session, seed_accounts["2200"].id) == 0


def test_a_po_turned_into_a_bill_posts_its_itc(client, db_session, seed_accounts):
    _recoverable(client)
    r = client.post("/api/vendors", json={"name": "Nursery",
                    "default_expense_account_id": seed_accounts["5100"].id})
    v = r.json()
    po = client.post(
        "/api/purchase-orders",
        json={
            "vendor_id": v["id"],
            "date": "2026-11-05",
            "tax_rate": HST,
            "lines": [{"description": "plant material", "quantity": 10, "rate": 45}],
        },
    )
    assert po.status_code == 201, po.text
    conv = client.post(f"/api/purchase-orders/{po.json()['id']}/convert-to-bill")
    assert conv.status_code == 200, conv.text
    assert _gl(db_session, _itc_id(db_session)) == Decimal("58.50")
    bill = db_session.query(Bill).filter_by(id=conv.json()["bill_id"]).one()
    assert Decimal(str(bill.total)) == Decimal("508.50")
    assert -_gl(db_session, seed_accounts["2000"].id) == Decimal("508.50")


def test_the_itc_account_cannot_be_renumbered(client, db_session, seed_accounts):
    _recoverable(client)
    v = _vendor(client)
    _bill(client, v["id"], seed_accounts)
    r = client.put(f"/api/accounts/{_itc_id(db_session)}", json={"account_number": "1351"})
    assert r.status_code >= 400
