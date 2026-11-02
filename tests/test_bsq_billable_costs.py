"""bsq: editing a cheque, and billable costs onto invoices (QuickBooks'
Choose Billable Time and Costs)."""

from decimal import Decimal

from tests.test_bsq_split_purchases import _cust, _gl, _subcontract


def _cheque(client, seed_accounts, sub, cust_id, amount="3257.05", billable=True, ref="1801"):
    tax = (Decimal(amount) * Decimal("0.13")).quantize(Decimal("0.01"))
    r = client.post("/api/expenses", json={
        "date": "2026-10-29", "paid_from_account_id": seed_accounts["1000"].id,
        "payee": "Urban Irrigation", "reference": ref,
        "amount": str(Decimal(amount) + tax), "tax_amount": str(tax),
        "lines": [{"account_id": sub.id, "amount": amount, "customer_id": cust_id,
                   "memo": "Lighting Invoice", "is_billable": billable}],
    })
    assert r.status_code == 201, r.text
    return r.json()


def _billable(client, cust_id):
    r = client.get(f"/api/billable-costs?customer_id={cust_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _invoice(client, cust_id, lines):
    r = client.post("/api/invoices", json={"customer_id": cust_id, "date": "2026-11-02", "tax_rate": 0.13, "lines": lines})
    return r


def test_a_billable_cheque_line_is_offered_billed_once_and_freed_by_a_void(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    c = _cust(client, "31 Lippincott St")
    _cheque(client, seed_accounts, sub, c["id"])
    _cheque(client, seed_accounts, sub, c["id"], amount="100", billable=False, ref="1802")
    costs = _billable(client, c["id"])
    assert [(x["payee"], x["memo"], x["amount"]) for x in costs] == [("Urban Irrigation", "Lighting Invoice", 3257.05)]
    cost = costs[0]
    r = _invoice(client, c["id"], [{"description": "Urban Irrigation - Lighting Invoice", "quantity": 1,
                                    "rate": 3257.05, "job_id": cost["job_id"], "billable_cost_id": cost["id"]}])
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["lines"][0]["billable_cost_id"] == cost["id"]
    assert _billable(client, c["id"]) == []
    # the same cost can't go on a second invoice
    r2 = _invoice(client, c["id"], [{"description": "again", "quantity": 1, "rate": 1, "billable_cost_id": cost["id"]}])
    assert r2.status_code == 400 and "already on another invoice" in r2.text
    # void the invoice: the cost is offered again
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    assert [x["id"] for x in _billable(client, c["id"])] == [cost["id"]]


def test_another_customers_cost_is_refused(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    a = _cust(client, "31 Lippincott St")
    b = _cust(client, "8 Other Road")
    _cheque(client, seed_accounts, sub, a["id"])
    cost = _billable(client, a["id"])[0]
    r = _invoice(client, b["id"], [{"description": "x", "quantity": 1, "rate": 1, "billable_cost_id": cost["id"]}])
    assert r.status_code == 400 and "another customer" in r.text


def test_editing_the_invoice_keeps_or_frees_its_costs(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    c = _cust(client, "31 Lippincott St")
    _cheque(client, seed_accounts, sub, c["id"])
    cost = _billable(client, c["id"])[0]
    inv = _invoice(client, c["id"], [{"description": "cost", "quantity": 1, "rate": 3257.05,
                                      "billable_cost_id": cost["id"]}]).json()
    line = {"description": "cost (edited)", "quantity": 1, "rate": 3257.05, "billable_cost_id": cost["id"]}
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": [line]})
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["billable_cost_id"] == cost["id"]
    assert _billable(client, c["id"]) == []
    r = client.put(f"/api/invoices/{inv['id']}", json={"lines": [{"description": "design", "quantity": 1, "rate": 500}]})
    assert r.status_code == 200, r.text
    assert [x["id"] for x in _billable(client, c["id"])] == [cost["id"]]


def test_a_cheque_can_be_opened_and_changed(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    a = _cust(client, "31 Lippincott St")
    b = _cust(client, "16 Carey Road")
    x = _cheque(client, seed_accounts, sub, a["id"], billable=False)
    bank_before = _gl(db_session, seed_accounts["1000"].id)
    r = client.put(f"/api/expenses/{x['id']}", json={
        "date": "2026-10-30", "paid_from_account_id": seed_accounts["1000"].id,
        "payee": "Urban Irrigation", "reference": "1801",
        "amount": "1130.00", "tax_amount": "130.00",
        "lines": [{"account_id": sub.id, "amount": "600", "customer_id": a["id"], "memo": "part 1"},
                  {"account_id": sub.id, "amount": "400", "customer_id": b["id"], "memo": "part 2"}],
    })
    assert r.status_code == 200, r.text
    y = r.json()
    assert y["id"] == x["id"] and y["date"] == "2026-10-30"
    assert (y["amount"], y["tax_amount"]) == (1130.0, 130.0)
    assert [(l["amount"], l["customer_job"]) for l in y["lines"]] == [(600.0, "31 Lippincott St"), (400.0, "16 Carey Road")]
    # the bank went down by the new amount, not both
    assert _gl(db_session, seed_accounts["1000"].id) - bank_before == Decimal("3680.47") - Decimal("1130.00")
    assert len(client.get("/api/expenses").json()) == 1


def test_a_billed_line_keeps_its_amount_when_the_cheque_is_edited(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    c = _cust(client, "31 Lippincott St")
    x = _cheque(client, seed_accounts, sub, c["id"])
    cost = _billable(client, c["id"])[0]
    assert _invoice(client, c["id"], [{"description": "c", "quantity": 1, "rate": 3257.05,
                                       "billable_cost_id": cost["id"]}]).status_code == 201
    base = {"date": "2026-10-29", "paid_from_account_id": seed_accounts["1000"].id,
            "payee": "Urban Irrigation", "reference": "1801"}
    # a new memo is fine; the billed line stays billed
    r = client.put(f"/api/expenses/{x['id']}", json={**base, "amount": "3680.47", "tax_amount": "423.42",
        "lines": [{"account_id": sub.id, "amount": "3257.05", "customer_id": c["id"], "memo": "new memo", "is_billable": True}]})
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0]["billed"] is True
    assert _billable(client, c["id"]) == []
    # a new amount on the billed line is refused
    r = client.put(f"/api/expenses/{x['id']}", json={**base, "amount": "1130.00", "tax_amount": "130.00",
        "lines": [{"account_id": sub.id, "amount": "1000", "customer_id": c["id"], "is_billable": True}]})
    assert r.status_code == 400 and "already been billed" in r.text


def test_a_void_cheques_cost_is_not_offered(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    c = _cust(client, "31 Lippincott St")
    x = _cheque(client, seed_accounts, sub, c["id"])
    assert client.post(f"/api/expenses/{x['id']}/void").status_code == 200
    assert _billable(client, c["id"]) == []


def test_a_billable_bill_line_is_offered_until_the_bill_is_voided(client, db_session, seed_accounts):
    sub = _subcontract(db_session)
    c = _cust(client, "16 Carey Road")
    v = client.post("/api/vendors", json={"name": "Decks and Fences by Design"}).json()
    b = client.post("/api/bills", json={"vendor_id": v["id"], "date": "2014-11-26", "tax_rate": 0.13,
        "lines": [{"account_id": sub.id, "customer_id": c["id"], "quantity": 1, "rate": 5694.91,
                   "description": "Balance", "is_billable": True}]}).json()
    costs = _billable(client, c["id"])
    assert [(x["payee"], x["amount"], x["source"]) for x in costs] == [("Decks and Fences by Design", 5694.91, "Bill")]
    # editing the bill's memo after billing keeps it billed
    assert _invoice(client, c["id"], [{"description": "x", "quantity": 1, "rate": 5694.91,
                                       "billable_cost_id": costs[0]["id"]}]).status_code == 201
    r = client.put(f"/api/bills/{b['id']}", json={"notes": "edited",
        "lines": [{"account_id": sub.id, "customer_id": c["id"], "quantity": 1, "rate": 5694.91,
                   "description": "Balance (edited)", "is_billable": True}]})
    assert r.status_code == 200, r.text
    assert _billable(client, c["id"]) == []
