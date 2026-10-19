"""bsq (Canada): the GST/HST return worksheet and recording it."""

from decimal import Decimal

from app.models.accounts import Account
from app.models.transactions import TransactionLine


def _gl(db, account_id):
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum((Decimal(str(r.debit or 0)) - Decimal(str(r.credit or 0)) for r in rows), Decimal(0))


def _acct(db, number):
    return db.query(Account).filter_by(account_number=number).one()


def _quarter(client, seed_accounts, seed_customer):
    client.put("/api/settings", json={"purchase_tax_recoverable": "true"})
    r = client.post("/api/invoices", json={
        "customer_id": seed_customer.id, "date": "2026-07-15", "tax_rate": 0.13,
        "lines": [{"description": "Landscape design", "quantity": 1, "rate": 1000}]})
    assert r.status_code == 201, r.text
    v = client.post("/api/vendors", json={"name": "Stone Co"}).json()
    r = client.post("/api/bills", json={
        "vendor_id": v["id"], "date": "2026-08-03", "tax_rate": 0.13,
        "lines": [{"description": "stone", "quantity": 1, "rate": 1250,
                   "account_id": seed_accounts["5100"].id}]})
    assert r.status_code == 201, r.text
    r = client.post("/api/expenses", json={
        "date": "2026-09-12", "payee": "Home Depot",
        "expense_account_id": seed_accounts["6000"].id,
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "113.00", "tax_amount": "13.00"})
    assert r.status_code == 201, r.text


Q3 = "start_date=2026-07-01&end_date=2026-09-30"


def test_the_worksheet_fills_the_return_lines(client, db_session, seed_accounts, seed_customer):
    _quarter(client, seed_accounts, seed_customer)
    d = client.get(f"/api/reports/hst-return?{Q3}").json()
    assert d["line_101"] == 1000.00 and d["line_101_rounded"] == 1000
    assert d["line_103"] == 130.00
    assert d["line_106"] == 175.50  # 162.50 on the bill + 13.00 on the expense
    assert d["line_109"] == -45.50
    assert d["refund"] == 45.50 and d["owing"] == 0
    assert d["filed_transaction_id"] is None
    # the next quarter is empty
    q4 = client.get("/api/reports/hst-return?start_date=2026-10-01&end_date=2026-12-31").json()
    assert q4["line_103"] == 0 and q4["line_106"] == 0


def test_recording_the_return_clears_both_hst_accounts(client, db_session, seed_accounts, seed_customer):
    _quarter(client, seed_accounts, seed_customer)
    bank_before = _gl(db_session, seed_accounts["1000"].id)
    r = client.post("/api/reports/hst-return/record", json={
        "start_date": "2026-07-01", "end_date": "2026-09-30", "date": "2026-10-20",
        "bank_account_id": seed_accounts["1000"].id})
    assert r.status_code == 200, r.text
    assert _gl(db_session, _acct(db_session, "2200").id) == 0
    assert _gl(db_session, _acct(db_session, "1350").id) == 0
    assert _gl(db_session, seed_accounts["1000"].id) - bank_before == Decimal("45.50")  # refund in
    # the worksheet still shows the period's activity, and that it is filed
    d = client.get(f"/api/reports/hst-return?{Q3}").json()
    assert d["line_103"] == 130.00 and d["line_106"] == 175.50
    assert d["filed_transaction_id"] == r.json()["transaction_id"]
    # the payment, dated in Q4, is not Q4 activity
    q4 = client.get("/api/reports/hst-return?start_date=2026-10-01&end_date=2026-12-31").json()
    assert q4["line_103"] == 0 and q4["line_106"] == 0
    # once only
    again = client.post("/api/reports/hst-return/record", json={
        "start_date": "2026-07-01", "end_date": "2026-09-30", "date": "2026-10-21",
        "bank_account_id": seed_accounts["1000"].id})
    assert again.status_code == 400


def test_an_amount_owing_is_paid_from_the_bank_and_a_void_reopens_the_period(
    client, db_session, seed_accounts, seed_customer
):
    client.put("/api/settings", json={"purchase_tax_recoverable": "true"})
    client.post("/api/invoices", json={
        "customer_id": seed_customer.id, "date": "2026-04-10", "tax_rate": 0.13,
        "lines": [{"description": "Design", "quantity": 1, "rate": 5000}]})
    q2 = {"start_date": "2026-04-01", "end_date": "2026-06-30"}
    d = client.get("/api/reports/hst-return", params=q2).json()
    assert d["line_109"] == 650.00 and d["owing"] == 650.00
    r = client.post("/api/reports/hst-return/record", json={
        **q2, "date": "2026-07-25", "bank_account_id": seed_accounts["1000"].id})
    assert r.status_code == 200, r.text
    assert _gl(db_session, seed_accounts["1000"].id) == Decimal("-650.00")
    assert _gl(db_session, _acct(db_session, "2200").id) == 0
    v = client.post(f"/api/reports/hst-return/{r.json()['transaction_id']}/void")
    assert v.status_code == 200, v.text
    assert _gl(db_session, _acct(db_session, "2200").id) == Decimal("-650.00")
    assert client.get("/api/reports/hst-return", params=q2).json()["filed_transaction_id"] is None
    # the void, dated in Q3, is not Q3 activity
    q3 = client.get(f"/api/reports/hst-return?{Q3}").json()
    assert q3["line_103"] == 0


def test_nothing_to_record_is_refused(client, db_session, seed_accounts):
    r = client.post("/api/reports/hst-return/record", json={
        "start_date": "2026-01-01", "end_date": "2026-03-31", "date": "2026-04-20",
        "bank_account_id": seed_accounts["1000"].id})
    assert r.status_code == 400
