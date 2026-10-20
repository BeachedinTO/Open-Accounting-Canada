"""bsq: purchases charged to a customer or a job (QuickBooks' Customer:Job).

A bill line, an expense or a card charge can name a job, or just a
customer: a customer alone is charged to the customer's own job (named
after the customer, made the first time and then reused), so job costing
and billable costs keep working.
"""

from app.models.bills import Bill
from app.models.jobs import Job
from app.models.transactions import Transaction


def _customer(client, name="Smith Residence"):
    r = client.post("/api/customers", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _vendor(client):
    r = client.post("/api/vendors", json={"name": "Ontario Stone Supply"})
    assert r.status_code == 201, r.text
    return r.json()


def _bill(client, vendor_id, seed_accounts, **line):
    r = client.post("/api/bills", json={
        "vendor_id": vendor_id, "date": "2026-11-03", "tax_rate": 0.13,
        "lines": [{"description": "armour stone", "quantity": 1, "rate": 1000,
                   "account_id": seed_accounts["5100"].id, **line}]})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_bill_line_for_a_customer_goes_to_the_customers_own_job(client, db_session, seed_accounts):
    c = _customer(client)
    v = _vendor(client)
    b1 = _bill(client, v["id"], seed_accounts, customer_id=c["id"], is_billable=True)
    jobs = db_session.query(Job).filter_by(customer_id=c["id"]).all()
    assert [j.name for j in jobs] == ["Smith Residence"]
    bill = db_session.get(Bill, b1["id"])
    assert bill.lines[0].job_id == jobs[0].id and bill.lines[0].is_billable
    # the posted cost carries the job, so job costing sees it
    txn = db_session.get(Transaction, bill.transaction_id)
    assert {ln.job_id for ln in txn.lines if ln.debit and ln.account_id == seed_accounts["5100"].id} == {jobs[0].id}
    # a second bill for the same customer reuses the job
    _bill(client, v["id"], seed_accounts, customer_id=c["id"])
    assert db_session.query(Job).filter_by(customer_id=c["id"]).count() == 1


def test_a_bill_line_for_a_named_job_keeps_that_job(client, db_session, seed_accounts):
    c = _customer(client)
    j = client.post("/api/jobs", json={"customer_id": c["id"], "name": "Backyard pool"})
    assert j.status_code == 201, j.text
    v = _vendor(client)
    b = _bill(client, v["id"], seed_accounts, job_id=j.json()["id"])
    assert db_session.get(Bill, b["id"]).lines[0].job_id == j.json()["id"]
    assert db_session.query(Job).filter_by(customer_id=c["id"]).count() == 1


def test_an_expense_and_a_card_charge_can_name_a_customer(client, db_session, seed_accounts):
    c = _customer(client, "Lee Residence")
    r = client.post("/api/expenses", json={
        "date": "2026-11-12", "payee": "Home Depot",
        "expense_account_id": seed_accounts["5100"].id,
        "paid_from_account_id": seed_accounts["1000"].id,
        "amount": "113.00", "tax_amount": "13.00", "customer_id": c["id"]})
    assert r.status_code == 201, r.text
    job = db_session.query(Job).filter_by(customer_id=c["id"]).one()
    assert db_session.get(Transaction, r.json()["id"]).job_id == job.id
    r = client.post("/api/cc-charges", json={
        "date": "2026-11-14", "payee": "Lee Valley", "account_id": seed_accounts["5100"].id,
        "amount": "56.50", "tax_amount": "6.50", "customer_id": c["id"]})
    assert r.status_code == 201, r.text
    assert db_session.get(Transaction, r.json()["transaction_id"]).job_id == job.id
    assert db_session.query(Job).filter_by(customer_id=c["id"]).count() == 1


def test_an_unknown_customer_is_refused(client, db_session, seed_accounts):
    v = _vendor(client)
    r = client.post("/api/bills", json={
        "vendor_id": v["id"], "date": "2026-11-03",
        "lines": [{"description": "x", "quantity": 1, "rate": 10,
                   "account_id": seed_accounts["5100"].id, "customer_id": 99999}]})
    assert r.status_code == 404
    assert db_session.query(Bill).count() == 0
