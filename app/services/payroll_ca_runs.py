# ============================================================================
# bsq: Canadian pay runs. A run lists employees with their hours; each stub
# is worked out by app/services/payroll_ca.py with the employee's
# year-to-date (processed runs of the same calendar year), vacation pay at
# the employee's percentage on top, and WSIB at the company's rate.
# Processing posts one journal entry:
#   Payroll Expenses        gross pay (wages + vacation pay)
#   Payroll Expenses        employer CPP, CPP2 and EI
#   WSIB - Insurance Expense  WSIB premium
#     Payroll Liabilities   employee CPP, CPP2, EI, income tax, the
#                           employer shares and WSIB (what's remitted)
#     the bank              net pay
# Voiding posts the mirror image. Amounts are kept on each stub, so the
# PD7A, WSIB and T4 reports read the stubs.
# ============================================================================

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.payroll import Employee, PayRun, PayRunStatus, PayStub
from app.services import payroll_ca as engine

D = Decimal
CENT = D("0.01")
REGION = "CA"
PERIODS = 26  # biweekly


def _r(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def _setting(db, key, default=""):
    from app.services.settings_service import get_setting_raw

    v = get_setting_raw(db, key)
    return default if v in (None, "") else v


def wsib_rate(db) -> Decimal:
    """WSIB premium per $100 of insurable earnings (bsq: 4.31)."""
    try:
        return D(_setting(db, "wsib_rate", "0"))
    except Exception:
        return D(0)


def _account(db, names, numbers=(), create=None) -> Account:
    for n in numbers:
        a = db.query(Account).filter(Account.account_number == n).first()
        if a:
            return a
    for n in names:
        a = db.query(Account).filter(Account.name == n).first()
        if a:
            return a
    if create is None:
        raise HTTPException(status_code=400, detail=f"There's no {names[0]} account in the chart.")
    a = Account(name=names[0], account_type=create)
    db.add(a)
    db.flush()
    return a


def accounts(db) -> dict:
    return {
        "expense": _account(db, ["Payroll Expenses", "Wages & Salaries"], ("66000", "6110")),
        "liability": _account(db, ["Payroll Liabilities"], ("2300",)),
        "wsib": _account(db, ["WSIB - Insurance Expense", "WSIB Expense"], create=AccountType.EXPENSE),
    }


def ytd_for(db, employee_id: int, pay_date: date, exclude_run_id=None) -> dict:
    """The employee's year-to-date from processed Canadian runs paid earlier
    in the same calendar year."""
    q = (db.query(PayStub).join(PayRun, PayRun.id == PayStub.pay_run_id)
         .filter(PayStub.employee_id == employee_id, PayRun.region == REGION,
                 PayRun.status == PayRunStatus.PROCESSED,
                 PayRun.pay_date >= date(pay_date.year, 1, 1), PayRun.pay_date <= pay_date))
    if exclude_run_id:
        q = q.filter(PayRun.id != exclude_run_id)
    out = {k: D(0) for k in ("gross", "vacation_pay", "cpp", "cpp2", "ei", "income_tax", "net", "hours", "wsib")}
    for s in q.all():
        out["gross"] += D(s.gross_pay or 0)
        out["vacation_pay"] += D(s.vacation_pay or 0)
        out["cpp"] += D(s.cpp or 0)
        out["cpp2"] += D(s.cpp2 or 0)
        out["ei"] += D(s.ei or 0)
        out["income_tax"] += D(s.federal_tax or 0) + D(s.state_tax or 0)
        out["net"] += D(s.net_pay or 0)
        out["hours"] += D(s.hours or 0)
        out["wsib"] += D(s.wsib or 0)
    return out


def _fill(db, run: PayRun, stub: PayStub, emp: Employee):
    hours = D(stub.hours or 0)
    rate = D(stub.rate if stub.rate is not None else (emp.pay_rate or 0))
    wages = _r(hours * rate)
    vac = _r(wages * D(emp.vacation_pct or 0) / 100)
    gross = wages + vac
    y = ytd_for(db, emp.id, run.pay_date, exclude_run_id=run.id)
    pay = engine.calculate(
        gross, run.pay_date, PERIODS,
        engine.Employee(td1_federal=emp.td1_federal, td1_ontario=emp.td1_ontario,
                        birth_date=emp.birth_date, cpp_exempt=bool(emp.cpp_exempt),
                        ei_exempt=bool(emp.ei_exempt), extra_tax=D(emp.extra_withholding or 0)),
        engine.YTD(cpp=y["cpp"], cpp2=y["cpp2"], ei=y["ei"], pensionable=y["gross"], insurable=y["gross"]),
    )
    stub.rate = rate
    stub.regular_hours = hours
    stub.gross_pay = gross
    stub.vacation_pay = vac
    stub.cpp, stub.cpp2, stub.ei = pay.cpp, pay.cpp2, pay.ei
    stub.employer_cpp, stub.employer_cpp2, stub.employer_ei = pay.employer_cpp, pay.employer_cpp2, pay.employer_ei
    stub.federal_tax, stub.state_tax = pay.federal_tax, pay.ontario_tax
    stub.wsib = _r(gross * wsib_rate(db) / 100)
    stub.net_pay = pay.net
    stub.work_state = "ON"
    stub.detail_json = json.dumps({"wages": str(wages), "table": pay.detail.get("table")})


def recalc(db, run: PayRun):
    """Work every stub of a draft run out again."""
    if run.status != PayRunStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only a draft pay run can change.")
    for stub in run.stubs:
        _fill(db, run, stub, db.get(Employee, stub.employee_id))
    _totals(run)


def _totals(run: PayRun):
    s = run.stubs
    run.total_gross = sum((D(x.gross_pay) for x in s), D(0))
    run.total_net = sum((D(x.net_pay) for x in s), D(0))
    run.total_taxes = sum((D(x.cpp) + D(x.cpp2) + D(x.ei) + D(x.federal_tax) + D(x.state_tax) for x in s), D(0))
    run.total_employer_taxes = sum((D(x.employer_cpp) + D(x.employer_cpp2) + D(x.employer_ei) + D(x.wsib) for x in s), D(0))


def create_run(db, period_start: date, period_end: date, pay_date: date, lines: list) -> PayRun:
    """`lines`: [{employee_id, hours, rate?}]"""
    try:
        engine.table_for(pay_date)
    except engine.TableMissing as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not lines:
        raise HTTPException(status_code=400, detail="Add at least one employee to the pay run.")
    run = PayRun(period_start=period_start, period_end=period_end, pay_date=pay_date,
                 status=PayRunStatus.DRAFT, region=REGION)
    db.add(run)
    db.flush()
    seen = set()
    for ln in lines:
        emp = db.get(Employee, int(ln["employee_id"]))
        if emp is None:
            raise HTTPException(status_code=404, detail="Employee not found")
        if emp.id in seen:
            raise HTTPException(status_code=400, detail=f"{emp.first_name} {emp.last_name} is on the pay run twice.")
        seen.add(emp.id)
        hours = ln.get("hours")
        if hours in (None, ""):
            hours = emp.default_hours or 0
        stub = PayStub(pay_run_id=run.id, employee_id=emp.id, hours=D(str(hours)),
                       rate=D(str(ln["rate"])) if ln.get("rate") not in (None, "") else None)
        db.add(stub)
        run.stubs.append(stub)
    db.flush()
    recalc(db, run)
    db.flush()
    return run


def process_run(db, run: PayRun, bank_account_id: int):
    from app.services.accounting import create_journal_entry
    from app.services.closing_date import check_closing_date

    if run.region != REGION:
        raise HTTPException(status_code=400, detail="Not a Canadian pay run.")
    if run.status != PayRunStatus.DRAFT:
        raise HTTPException(status_code=400, detail=f"This pay run is {run.status.value} already.")
    check_closing_date(db, run.pay_date)
    bank = db.get(Account, bank_account_id)
    if bank is None or bank.account_type not in (AccountType.ASSET, AccountType.LIABILITY):
        raise HTTPException(status_code=400, detail="Choose the bank account the pay comes out of.")
    recalc(db, run)  # the year-to-date as it stands now
    a = accounts(db)
    gross = sum((D(s.gross_pay) for s in run.stubs), D(0))
    employer = sum((D(s.employer_cpp) + D(s.employer_cpp2) + D(s.employer_ei) for s in run.stubs), D(0))
    wsib = sum((D(s.wsib) for s in run.stubs), D(0))
    withheld = sum((D(s.cpp) + D(s.cpp2) + D(s.ei) + D(s.federal_tax) + D(s.state_tax) for s in run.stubs), D(0))
    net = sum((D(s.net_pay) for s in run.stubs), D(0))
    z = D(0)
    lines = [
        {"account_id": a["expense"].id, "debit": gross, "credit": z, "description": "Gross pay (wages and vacation pay)"},
    ]
    if employer:
        lines.append({"account_id": a["expense"].id, "debit": employer, "credit": z, "description": "Employer CPP, CPP2 and EI"})
    if wsib:
        lines.append({"account_id": a["wsib"].id, "debit": wsib, "credit": z, "description": "WSIB premium"})
    lines.append({"account_id": a["liability"].id, "debit": z, "credit": withheld + employer + wsib,
                  "description": "CPP, CPP2, EI and income tax to the Receiver General; WSIB"})
    if net:
        lines.append({"account_id": bank.id, "debit": z, "credit": net, "description": "Net pay"})
    txn = create_journal_entry(db, run.pay_date, f"Payroll {run.period_start:%Y-%m-%d} to {run.period_end:%Y-%m-%d}",
                               lines, source_type="payroll", source_id=run.id,
                               reference=f"Pay run {run.pay_date:%Y-%m-%d}")
    run.transaction_id = txn.id
    run.status = PayRunStatus.PROCESSED
    db.flush()
    return run


def void_run(db, run: PayRun):
    from app.models.transactions import Transaction
    from app.services.accounting import create_journal_entry, reversing_lines
    from app.services.closing_date import check_closing_date

    if run.status != PayRunStatus.PROCESSED:
        raise HTTPException(status_code=400, detail="Only a processed pay run can be voided.")
    later = (db.query(PayRun).filter(PayRun.region == REGION, PayRun.status == PayRunStatus.PROCESSED,
                                     PayRun.pay_date > run.pay_date,
                                     PayRun.pay_date < date(run.pay_date.year + 1, 1, 1)).first())
    if later is not None:
        raise HTTPException(status_code=400, detail=(
            f"The pay run of {later.pay_date:%Y-%m-%d} came after this one and counted it in its "
            "year-to-date. Void the later runs first, newest first."))
    check_closing_date(db, run.pay_date)
    txn = db.get(Transaction, run.transaction_id) if run.transaction_id else None
    if txn is not None:
        create_journal_entry(db, run.pay_date, f"VOID {txn.description}", reversing_lines(txn.lines),
                             source_type="payroll_void", source_id=run.id, reference=txn.reference or "")
    run.status = PayRunStatus.VOID
    db.flush()
    return run
