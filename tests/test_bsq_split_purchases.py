"""bsq: purchases paid directly (cheque, debit, cash, e-transfer) and credit
card charges, split over several expense types and customers, with one HST
amount for the receipt; and invoices that land on the customer's own job.
"""

from decimal import Decimal

from app.models.invoices import Invoice
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine


def _gl(db, account_id):
    rows = db.query(TransactionLine).filter_by(account_id=account_id).all()
    return sum((Decimal(str(r.debit or 0)) - Decimal(str(r.credit or 0)) for r in rows), Decimal(0))


def _cust(client, name):
    r = client.post("/api/customers", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _itc(client):
    return next(a["id"] for a in client.get("/api/accounts").json() if a["account_number"] == "1350")


def test_a_cheque_split_over_two_projects_and_two_expense_types(client, db_session, seed_accounts):
    a = _cust(client, "12 Elm Street")
    b = _cust(client, "48 Oak Avenue")
    r = client.post("/api/expenses", json={
        "date": "2026-11-05", "payee": "Home Depot", "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "339.00", "tax_amount": "39.00", "reference": "Cheque 2002",
        "lines": [
            {"account_id": seed_accounts["5100"].id, "amount": "200.00", "customer_id": a["id"], "memo": "pavers", "is_billable": True},
            {"account_id": seed_accounts["6800"].id, "amount": "100.00", "customer_id": b["id"], "memo": "saw blade"},
        ]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount"] == 339.00 and body["tax_amount"] == 39.00
    assert body["expense_account_name"] == "Split (2 lines)"
    assert [(l["account_name"], l["amount"], l["customer_job"]) for l in body["lines"]] == [
        (seed_accounts["5100"].name, 200.0, "12 Elm Street"),
        (seed_accounts["6800"].name, 100.0, "48 Oak Avenue"),
    ]
    assert _gl(db_session, seed_accounts["5100"].id) == Decimal("200.00")
    assert _gl(db_session, seed_accounts["6800"].id) == Decimal("100.00")
    assert _gl(db_session, _itc(client)) == Decimal("39.00")
    assert _gl(db_session, seed_accounts["1000"].id) == Decimal("-339.00")
    # each line carries its project's job
    jobs = {j.customer_id: j.id for j in db_session.query(Job).all()}
    txn = db_session.get(Transaction, body["id"])
    by_acct = {ln.account_id: ln for ln in txn.lines}
    assert by_acct[seed_accounts["5100"].id].job_id == jobs[a["id"]]
    assert by_acct[seed_accounts["5100"].id].is_billable
    assert by_acct[seed_accounts["6800"].id].job_id == jobs[b["id"]]
    # voiding takes it all back
    assert client.post(f"/api/expenses/{body['id']}/void").status_code == 200
    assert _gl(db_session, seed_accounts["1000"].id) == 0 and _gl(db_session, _itc(client)) == 0


def test_lines_that_do_not_add_up_are_refused(client, db_session, seed_accounts):
    r = client.post("/api/expenses", json={
        "date": "2026-11-05", "payee": "Home Depot", "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "339.00", "tax_amount": "39.00",
        "lines": [{"account_id": seed_accounts["5100"].id, "amount": "250.00"}]})
    assert r.status_code == 400
    assert "need to match" in r.json()["detail"]
    assert _gl(db_session, seed_accounts["1000"].id) == 0


def test_a_line_cannot_post_to_a_bank_account(client, db_session, seed_accounts):
    r = client.post("/api/expenses", json={
        "date": "2026-11-05", "payee": "x", "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "100.00", "lines": [{"account_id": seed_accounts["1010"].id, "amount": "100.00"}]})
    assert r.status_code == 400


def test_a_card_charge_split_over_projects(client, db_session, seed_accounts):
    a = _cust(client, "12 Elm Street")
    b = _cust(client, "48 Oak Avenue")
    r = client.post("/api/cc-charges", json={
        "date": "2026-11-06", "payee": "City of Toronto",
        "amount": "565.00", "tax_amount": "15.00",
        "lines": [
            {"account_id": seed_accounts["6950"].id, "amount": "435.00", "customer_id": a["id"], "memo": "building permit"},
            {"account_id": seed_accounts["5100"].id, "amount": "115.00", "customer_id": b["id"], "memo": "sand"},
        ]})
    assert r.status_code == 201, r.text
    assert _gl(db_session, seed_accounts["2100"].id) == Decimal("-565.00")
    assert _gl(db_session, _itc(client)) == Decimal("15.00")
    row = next(x for x in client.get("/api/cc-charges").json() if x["id"] == r.json()["transaction_id"])
    assert row["amount"] == 565.00 and row["account_name"] == "Split (2 lines)"
    assert [l["customer_job"] for l in row["lines"]] == ["12 Elm Street", "48 Oak Avenue"]


def _invoice_job_on(db_session):
    from app.services.settings_service import set_setting

    set_setting(db_session, "invoice_customer_job", "true")
    db_session.commit()


def test_an_invoice_goes_to_the_customers_own_job_with_its_costs(client, db_session, seed_accounts):
    _invoice_job_on(db_session)
    a = _cust(client, "12 Elm Street")
    r = client.post("/api/invoices", json={"customer_id": a["id"], "date": "2026-11-02", "tax_rate": 0.13,
        "lines": [{"description": "Design", "quantity": 1, "rate": 5000}]})
    assert r.status_code == 201, r.text
    client.post("/api/expenses", json={
        "date": "2026-11-05", "payee": "Home Depot", "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "226.00", "tax_amount": "26.00",
        "lines": [{"account_id": seed_accounts["5100"].id, "amount": "200.00", "customer_id": a["id"]}]})
    job = db_session.query(Job).filter_by(customer_id=a["id"]).one()
    assert db_session.get(Invoice, r.json()["id"]).job_id == job.id
    rows = client.get("/api/reports/job-profitability?start_date=2026-01-01&end_date=2026-12-31").json()
    rows = rows.get("jobs", rows) if isinstance(rows, dict) else rows
    mine = next(x for x in rows if x["job_id"] == job.id)
    assert mine["income"] == 5000.0 and mine["cogs"] == 200.0


def test_the_sample_cheque_1761_five_projects(client, db_session, seed_accounts):
    """Alex's QuickBooks cheque: one subcontractor, five projects, HST on
    every line. Lines 22,028.69 + HST 2,863.73 = 24,892.42."""
    from app.models.accounts import Account

    sub = db_session.query(Account).filter(Account.account_number == "5300").first()
    if sub is None:
        sub = Account(name="Subcontractor Costs", account_number="5300", account_type="cogs")
        db_session.add(sub)
        db_session.commit()
    bank = db_session.query(Account).filter(Account.account_number == "1000").first()
    amounts = ["3900.00", "9610.00", "525.00", "7756.45", "237.24"]
    lines = []
    for i, a in enumerate(amounts):
        c = _cust(client, f"{i + 1} Sample Street")
        lines.append({"account_id": sub.id, "amount": a, "customer_id": c["id"], "is_billable": True})
    r = client.post("/api/expenses", json={
        "date": "2025-12-11", "paid_from_account_id": bank.id, "payee": "Woodrock",
        "amount": "24892.42", "tax_amount": "2863.73", "reference": "1761", "lines": lines,
    })
    assert r.status_code == 201, r.text
    x = r.json()
    assert x["reference"] == "1761"
    assert x["date"] == "2025-12-11"
    assert x["tax_amount"] == 2863.73
    assert x["amount"] == 24892.42
    assert [l["amount"] for l in x["lines"]] == [3900.0, 9610.0, 525.0, 7756.45, 237.24]
    assert [l["customer_job"] for l in x["lines"]] == [f"{i} Sample Street" for i in range(1, 6)]
    assert x["memo"] == ""


def test_neighbouring_addresses_are_not_duplicates():
    from app.services.duplicate_detection import similarity

    assert similarity("12 Elm Street", "14 Elm Street") == 0.0
    assert similarity("35 Example Crescent", "35 Example Cres.") >= 0.85


def _inv(client, cid, **kw):
    r = client.post("/api/invoices", json={"customer_id": cid, "date": "2026-11-02",
        "lines": [{"description": "Design", "quantity": 1, "rate": 100}], **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_the_own_job_default_is_off_unless_set(client, db_session, seed_accounts):
    a = _cust(client, "3 Birch Road")
    assert _inv(client, a["id"]).get("job_id") is None


def test_a_customer_with_real_jobs_is_left_alone(client, db_session, seed_accounts):
    _invoice_job_on(db_session)
    a = _cust(client, "5 Cedar Lane")
    assert client.post("/api/jobs", json={"customer_id": a["id"], "name": "Front yard"}).status_code in (200, 201)
    assert _inv(client, a["id"]).get("job_id") is None


def test_moving_an_invoice_moves_it_to_the_new_customers_own_job(client, db_session, seed_accounts):
    from app.models.jobs import Job

    _invoice_job_on(db_session)
    a = _cust(client, "7 Maple Drive")
    b = _cust(client, "9 Pine Court")
    inv = _inv(client, a["id"])
    r = client.put(f"/api/invoices/{inv['id']}", json={"customer_id": b["id"]})
    assert r.status_code == 200, r.text
    job = db_session.get(Job, r.json()["job_id"])
    assert job.customer_id == b["id"] and job.name == "9 Pine Court"


def _subcontract(db_session):
    from app.models.accounts import Account

    sub = db_session.query(Account).filter(Account.account_number == "5300").first()
    if sub is None:
        sub = Account(name="Subcontractor Costs", account_number="5300", account_type="cogs")
        db_session.add(sub)
        db_session.commit()
    return sub


def test_the_sample_bill_decks_and_fences(client, db_session, seed_accounts):
    """Alex's QuickBooks bill: one Subcontracted Services line for 16 Carey
    Road, tax code H. 5,694.91 + HST 740.34 = 6,435.25."""
    sub = _subcontract(db_session)
    c = _cust(client, "16 Carey Road")
    v = client.post("/api/vendors", json={"name": "Decks and Fences by Design"}).json()
    r = client.post("/api/bills", json={
        "vendor_id": v["id"], "date": "2014-11-26", "due_date": "2014-12-06", "tax_rate": 0.13,
        "lines": [{"account_id": sub.id, "customer_id": c["id"], "quantity": 1, "rate": 5694.91,
                   "description": "Balance of 16 Carey Rd. ( $11,435.25 - $5,000 deposit)", "is_taxable": True}],
    })
    assert r.status_code == 201, r.text
    b = r.json()
    assert Decimal(str(b["tax_amount"])) == Decimal("740.34")
    assert Decimal(str(b["total"])) == Decimal("6435.25")
    assert b["due_date"] == "2014-12-06"
    assert b["lines"][0]["is_taxable"] is True
    assert _gl(db_session, _itc(client)) == Decimal("740.34")
    html = client.get(f"/api/bills/{b['id']}/print-preview").text
    for text in ("Ref. No.", "Expenses", "16 Carey Road", "(ITC)", "Input Tax Credit", "740.34", "6,435.25", "Bill Total"):
        assert text in html, text


def test_a_bill_line_without_hst_is_not_taxed(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    v = client.post("/api/vendors", json={"name": "City of Toronto"}).json()
    r = client.post("/api/bills", json={
        "vendor_id": v["id"], "date": "2026-11-02", "tax_rate": 0.13,
        "lines": [
            {"account_id": sub.id, "quantity": 1, "rate": 1000, "is_taxable": True},
            {"account_id": sub.id, "quantity": 1, "rate": 500, "is_taxable": False},
        ],
    })
    assert r.status_code == 201, r.text
    assert Decimal(str(r.json()["tax_amount"])) == Decimal("130.00")
    assert Decimal(str(r.json()["total"])) == Decimal("1630.00")


def test_the_sample_cheque_864_two_projects(client, db_session, seed_accounts):
    """Alex's QuickBooks cheque 864: 221.17 + 4,249.50, tax code H,
    Total Taxes 581.19, cheque 5,051.86."""
    sub = _subcontract(db_session)
    a = _cust(client, "493 Brunswick Avenue")
    b = _cust(client, "34 Oswald Crescent")
    r = client.post("/api/expenses", json={
        "date": "2015-10-20", "paid_from_account_id": seed_accounts["1000"].id,
        "payee": "Decks and Fences by Design", "reference": "864",
        "amount": "5051.86", "tax_amount": "581.19",
        "lines": [
            {"account_id": sub.id, "amount": "221.17", "customer_id": a["id"], "memo": "repair under step and fence"},
            {"account_id": sub.id, "amount": "4249.50", "customer_id": b["id"], "memo": "P/T steps and landing/skirting"},
        ],
    })
    assert r.status_code == 201, r.text
    x = r.json()
    assert (x["reference"], x["amount"], x["tax_amount"]) == ("864", 5051.86, 581.19)
