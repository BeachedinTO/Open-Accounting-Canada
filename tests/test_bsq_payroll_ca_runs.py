"""bsq: Canadian pay runs end to end: employees, a run, posting, stubs,
PD7A, WSIB, T4, voiding."""

from decimal import Decimal as D

from app.services.settings_service import set_setting

EMP = {"birth_date": "1990-05-01", "address1": "1 Test St", "city": "Toronto", "postal_code": "M4H 1M5",
       "td1_federal": None, "td1_ontario": None}


def _emp(client, first, last, sin, rate, hours=80, vac=0, **kw):
    r = client.post("/api/payroll/ca/employees", json={**EMP, "first_name": first, "last_name": last, "sin": sin,
                                                      "pay_rate": rate, "default_hours": hours, "vacation_pct": vac, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _setup(client, db_session):
    set_setting(db_session, "wsib_rate", "4.31")
    db_session.commit()
    a = _emp(client, "Orontes", "Mejia", "046 454 286", 36)
    b = _emp(client, "Sakiko", "Homma", "130 692 544", 24, hours=76, vac=4)
    return a, b


def test_a_sin_must_be_valid_and_address_and_birth_date_given(client, seed_accounts):
    r = client.post("/api/payroll/ca/employees", json={**EMP, "first_name": "A", "last_name": "B", "sin": "123 456 789", "pay_rate": 20})
    assert r.status_code == 400 and "SIN" in r.text
    r = client.post("/api/payroll/ca/employees", json={**EMP, "first_name": "A", "last_name": "B", "sin": "046 454 286", "pay_rate": 20, "address1": ""})
    assert r.status_code == 400 and "address" in r.text
    e = _emp(client, "A", "B", "046454286", 20)
    assert e["sin_display"] == "046 454 286"


def test_a_pay_run_posts_and_reports(client, db_session, seed_accounts):
    a, b = _setup(client, db_session)
    r = client.post("/api/payroll/ca/runs", json={"period_start": "2026-10-17", "period_end": "2026-10-30",
        "pay_date": "2026-11-06", "lines": [{"employee_id": a["id"]}, {"employee_id": b["id"]}]})
    assert r.status_code == 201, r.text
    run = r.json()
    sak = next(s for s in run["stubs"] if s["employee_id"] == b["id"])
    assert sak["wages"] == 1824.00 and sak["vacation_pay"] == 72.96 and sak["gross"] == 1896.96
    assert sak["cpp"] == round((1896.96 - 3500 / 26) * 0.0595, 2)
    assert sak["wsib"] == round(1896.96 * 0.0431, 2)
    bank = seed_accounts["1000"].id
    r = client.post(f"/api/payroll/ca/runs/{run['id']}/process", json={"bank_account_id": bank})
    assert r.status_code == 200, r.text
    run = r.json()
    from tests.test_bsq_split_purchases import _gl
    from app.models.accounts import Account
    liab = db_session.query(Account).filter(Account.name == "Payroll Liabilities").one()
    owed = sum(s["cpp"] + s["cpp2"] + s["ei"] + s["income_tax"] + s["employer_cpp"] + s["employer_cpp2"]
               + s["employer_ei"] + s["wsib"] for s in run["stubs"])
    assert _gl(db_session, liab.id) == -D(str(round(owed, 2)))
    assert _gl(db_session, bank) == -D(str(run["total_net"]))
    # the stub prints
    r = client.get(f"/api/payroll/ca/runs/{run['id']}/stubs/{sak['id']}/pdf")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    # PD7A for November: CPP x2, EI x2.4, tax
    p = client.get("/api/payroll/ca/reports/pd7a?year=2026&month=11").json()
    cpp = sum(s["cpp"] * 2 for s in run["stubs"])
    assert abs(p["cpp"] - cpp) < 0.01 and p["due_date"] == "2026-12-15" and p["employees_last_pay"] == 2
    assert abs(p["total"] - (owed - sum(s["wsib"] for s in run["stubs"]))) < 0.01
    w = client.get("/api/payroll/ca/reports/wsib?year=2026&quarter=4").json()
    assert w["rate"] == 4.31 and abs(w["insurable_earnings"] - run["total_gross"]) < 0.01
    t4 = client.get("/api/payroll/ca/reports/t4?year=2026").json()
    assert t4["summary"]["slips"] == 2 and abs(t4["summary"]["box_14"] - run["total_gross"]) < 0.01
    assert "Homma, Sakiko" in client.get("/api/payroll/ca/reports/t4.csv?year=2026").text


def test_year_to_date_carries_and_voids_go_newest_first(client, db_session, seed_accounts):
    a, _ = _setup(client, db_session)
    bank = seed_accounts["1000"].id
    ids = []
    for start, end, pay in (("2026-10-03", "2026-10-16", "2026-10-23"), ("2026-10-17", "2026-10-30", "2026-11-06")):
        run = client.post("/api/payroll/ca/runs", json={"period_start": start, "period_end": end, "pay_date": pay,
                                                         "lines": [{"employee_id": a["id"]}]}).json()
        run = client.post(f"/api/payroll/ca/runs/{run['id']}/process", json={"bank_account_id": bank}).json()
        ids.append(run["id"])
    assert run["stubs"][0]["ytd"]["gross"] == 5760.00
    r = client.post(f"/api/payroll/ca/runs/{ids[0]}/void")
    assert r.status_code == 400 and "newest first" in r.text
    assert client.post(f"/api/payroll/ca/runs/{ids[1]}/void").json()["status"] == "void"
    assert client.post(f"/api/payroll/ca/runs/{ids[0]}/void").json()["status"] == "void"
    from tests.test_bsq_split_purchases import _gl
    assert _gl(db_session, bank) == 0


def test_a_pay_dated_in_2027_waits_for_cras_tables(client, db_session, seed_accounts):
    a, _ = _setup(client, db_session)
    r = client.post("/api/payroll/ca/runs", json={"period_start": "2026-12-19", "period_end": "2027-01-01",
                                                   "pay_date": "2027-01-08", "lines": [{"employee_id": a["id"]}]})
    assert r.status_code == 400 and "not loaded" in r.text
