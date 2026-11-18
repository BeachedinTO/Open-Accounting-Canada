"""bsq: the weekly timesheet in QuickBooks' layout (Customer:Job, Service Item,
Payroll Item, Notes, Monday to Sunday, Total, Billable) and billable time
onto invoices (QuickBooks' Choose Billable Time and Costs, Time tab)."""

from decimal import Decimal

from app.models.items import Item, ItemType
from app.models.payroll import Employee
from tests.test_bsq_split_purchases import _cust

WEEK = "2026-09-14"   # a Monday, as on Jiffy's QuickBooks timesheet


def _emp(db):
    e = Employee(first_name="Hoi Lun", last_name="Lee", pay_rate=Decimal("41.12"), is_active=True)
    db.add(e)
    db.commit()
    return e


def _item(db, name="Landscape Consultation", rate="150"):
    it = Item(name=name, item_type=ItemType.SERVICE, rate=Decimal(rate), is_taxable=True, is_active=True)
    db.add(it)
    db.commit()
    return it


def _row(hours, customer_id=None, job_id=None, item=None, notes=None, billable=False):
    return {"customer_id": customer_id, "job_id": job_id, "service_item_id": item.id if item else None,
            "pay_type": "regular", "notes": notes, "billable": billable, "hours": hours}


def _save(client, emp, rows, week=WEEK):
    return client.put("/api/time-entries/week", json={"employee_id": emp.id, "week_start": week, "rows": rows})


def test_a_week_saves_one_entry_a_day_and_reads_back_as_rows(client, db_session):
    emp = _emp(db_session)
    consult = _item(db_session)
    glen = _cust(client, "135 Glen Road")
    admin = _cust(client, "2026 - Office Admin")
    r = _save(client, emp, [
        _row([0, 3, 2, 2.5, 0, 0, 0], customer_id=admin["id"], item=consult),
        _row([0, 0, 0, 3.5, 0, 0, 0], customer_id=glen["id"], item=consult,
             notes="Week of Aug. 24 - client emails, revisions for ZAP", billable=True),
        _row([0, 0, 0, 0, 0, 0, 0], customer_id=glen["id"], item=consult),   # empty: no time
    ])
    assert r.status_code == 200, r.text
    week = r.json()
    assert week["week_start"] == WEEK and week["days"][6] == "2026-09-20"
    assert len(week["rows"]) == 2
    rows = {tuple(x["hours"]): x for x in week["rows"]}
    assert rows[(0, 3, 2, 2.5, 0, 0, 0)]["billable"] is False
    glen_row = rows[(0, 0, 0, 3.5, 0, 0, 0)]
    assert glen_row["billable"] is True and glen_row["notes"].startswith("Week of Aug. 24")
    # any day of the week opens the same sheet
    again = client.get(f"/api/time-entries/week?employee_id={emp.id}&week_of=2026-09-17").json()
    assert sorted(tuple(x["hours"]) for x in again["rows"]) == sorted(rows)
    # saving again replaces, not adds
    r = _save(client, emp, [_row([1, 0, 0, 0, 0, 0, 0], customer_id=admin["id"], item=consult)])
    assert r.status_code == 200 and [x["hours"] for x in r.json()["rows"]] == [[1, 0, 0, 0, 0, 0, 0]]


def test_billable_needs_a_job_and_a_day_has_24_hours(client, db_session):
    emp = _emp(db_session)
    r = _save(client, emp, [_row([1, 0, 0, 0, 0, 0, 0], billable=True)])
    assert r.status_code == 400 and "billable" in r.text
    c = _cust(client, "52 Brumwell Street")
    r = _save(client, emp, [_row([20, 0, 0, 0, 0, 0, 0], customer_id=c["id"]),
                            _row([5, 0, 0, 0, 0, 0, 0], customer_id=c["id"], notes="x")])
    assert r.status_code == 400 and "more than a day" in r.text
    r = _save(client, emp, [], week="2026-09-15")
    assert r.status_code == 400 and "Monday" in r.text


def test_billable_time_goes_on_the_invoice_once_and_locks_until_freed(client, db_session, seed_accounts):
    emp = _emp(db_session)
    consult = _item(db_session)
    glen = _cust(client, "135 Glen Road")
    other = _cust(client, "239 Evelyn Avenue")
    notes = "Week of Aug. 24 - client emails, revisions for ZAP"
    rows = [_row([0, 0, 0, 3.5, 0, 0, 0], customer_id=glen["id"], item=consult, notes=notes, billable=True),
            _row([0, 5, 5, 0, 0, 0, 0], customer_id=glen["id"], item=consult)]   # not billable
    assert _save(client, emp, rows).status_code == 200
    time = client.get(f"/api/billable-costs/time?customer_id={glen['id']}").json()
    assert len(time) == 1
    t = time[0]
    assert (t["date"], t["hours"], t["rate"], t["amount"], t["notes"], t["item_name"], t["employee"]) == \
        ("2026-09-17", 3.5, 150.0, 525.0, notes, "Landscape Consultation", "Hoi Lun Lee")
    assert client.get(f"/api/billable-costs/time?customer_id={other['id']}").json() == []

    line = {"item_id": t["item_id"], "description": notes, "quantity": 3.5, "rate": 150,
            "job_id": t["job_id"], "billable_time_id": t["id"]}
    r = client.post("/api/invoices", json={"customer_id": other["id"], "date": "2026-11-02", "tax_rate": 0.13,
                                           "lines": [dict(line, job_id=None)]})
    assert r.status_code == 400 and "another customer" in r.text
    r = client.post("/api/invoices", json={"customer_id": glen["id"], "date": "2026-11-02", "tax_rate": 0.13,
                                           "lines": [line]})
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["lines"][0]["billable_time_id"] == t["id"]
    assert client.get(f"/api/billable-costs/time?customer_id={glen['id']}").json() == []

    # the billed hours can't be changed or dropped on the timesheet...
    changed = [dict(rows[0], hours=[0, 0, 0, 4, 0, 0, 0]), rows[1]]
    r = _save(client, emp, changed)
    assert r.status_code == 400 and f"billed on invoice {inv['invoice_number']}" in r.text
    # ...but the rest of the week still can
    r = _save(client, emp, [rows[0], dict(rows[1], hours=[0, 6, 5, 0, 0, 0, 0])])
    assert r.status_code == 200, r.text
    billed_row = next(x for x in r.json()["rows"] if x["billable"])
    assert billed_row["locked"][3].startswith("billed on invoice")
    assert client.delete(f"/api/time-entries/{t['id']}").status_code == 400

    # voiding the invoice frees the time
    assert client.post(f"/api/invoices/{inv['id']}/void").status_code == 200
    assert [x["id"] for x in client.get(f"/api/billable-costs/time?customer_id={glen['id']}").json()] == [t["id"]]
