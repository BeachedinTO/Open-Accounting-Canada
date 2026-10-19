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


def test_switched_off_tax_stays_in_the_cost(client, db_session, seed_accounts):
    # bsq: on by default now; a company can still switch it off
    _recoverable(client, on=False)
    v = _vendor(client)
    b = _bill(client, v["id"], seed_accounts)
    assert Decimal(b["total"]) == Decimal("1412.50")
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("1130.00")
    assert _gl(db_session, _itc_id(db_session)) == 0


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


# ── Direct payments: cheque / e-transfer expenses and credit-card charges ──


def _expense(client, seed_accounts, amount="113.00", tax="13.00", **extra):
    body = {
        "date": "2026-11-12",
        "payee": "Home Depot",
        "expense_account_id": seed_accounts["6000"].id,
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": amount,
        "tax_amount": tax,
        "reference": "e-transfer",
    }
    body.update(extra)
    return client.post("/api/expenses", json=body)


def test_an_expense_paid_by_cheque_or_etransfer_claims_its_hst(
    client, db_session, seed_accounts
):
    _recoverable(client)
    r = _expense(client, seed_accounts)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount"] == 113.00  # what left the bank
    assert body["tax_amount"] == 13.00
    assert body["expense_account_name"] == seed_accounts["6000"].name
    assert _gl(db_session, seed_accounts["6000"].id) == Decimal("100.00")
    assert _gl(db_session, _itc_id(db_session)) == Decimal("13.00")
    assert _gl(db_session, seed_accounts["1000"].id) == Decimal("-113.00")
    # voiding takes it all back
    assert client.post(f"/api/expenses/{body['id']}/void").status_code == 200
    assert _gl(db_session, _itc_id(db_session)) == 0
    assert _gl(db_session, seed_accounts["1000"].id) == 0


def test_an_expense_without_hst_is_unchanged(client, db_session, seed_accounts):
    _recoverable(client)
    r = _expense(client, seed_accounts, tax="0")
    assert r.status_code == 201, r.text
    assert r.json()["tax_amount"] == 0
    assert _gl(db_session, seed_accounts["6000"].id) == Decimal("113.00")
    assert _gl(db_session, _itc_id(db_session)) == 0


def test_hst_on_an_expense_is_refused_unless_the_setting_is_on(
    client, db_session, seed_accounts
):
    _recoverable(client, on=False)
    r = _expense(client, seed_accounts)
    assert r.status_code == 400
    assert "Tax on Purchases" in r.text
    assert _gl(db_session, seed_accounts["1000"].id) == 0


def test_hst_cannot_be_the_whole_amount_or_negative(client, db_session, seed_accounts):
    _recoverable(client)
    assert _expense(client, seed_accounts, amount="13.00", tax="13.00").status_code == 400
    assert _expense(client, seed_accounts, tax="-1").status_code == 400


def test_a_credit_card_charge_claims_its_hst(client, db_session, seed_accounts):
    _recoverable(client)
    r = client.post(
        "/api/cc-charges",
        json={
            "date": "2026-11-14",
            "payee": "Lee Valley",
            "account_id": seed_accounts["6000"].id,
            "amount": "56.50",
            "tax_amount": "6.50",
        },
    )
    assert r.status_code == 201, r.text
    assert _gl(db_session, seed_accounts["6000"].id) == Decimal("50.00")
    assert _gl(db_session, _itc_id(db_session)) == Decimal("6.50")
    assert _gl(db_session, seed_accounts["2100"].id) == Decimal("-56.50")
    row = next(x for x in client.get("/api/cc-charges").json()
               if x["id"] == r.json()["transaction_id"])
    assert row["amount"] == 56.50 and row["tax_amount"] == 6.50
    assert row["account_name"] == seed_accounts["6000"].name
    assert client.post(f"/api/cc-charges/{row['id']}/void").status_code == 200
    assert _gl(db_session, _itc_id(db_session)) == 0
