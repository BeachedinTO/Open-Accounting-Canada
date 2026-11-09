# ============================================================================
# bsq: Canadian payroll API (under /api/payroll, so admin-only like the rest
# of payroll). Employees' Canadian details, pay runs, stubs, and the PD7A,
# WSIB and T4 reports.
# ============================================================================

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from app.schemas.common import StrictModel as BaseModel  # refuses unknown fields
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payroll import Employee, PayRun, PayRunStatus, PayStub
from app.services import payroll_ca_runs as runs
from app.services.crypto import decrypt_value, encrypt_value

router = APIRouter(prefix="/api/payroll/ca", tags=["payroll-canada"])
D = Decimal


# ── employees ──────────────────────────────────────────────────────────────

class EmployeeIn(BaseModel):
    first_name: str
    last_name: str
    sin: Optional[str] = None          # 9 digits; left out on an edit = unchanged
    birth_date: Optional[date] = None
    address1: Optional[str] = None
    address2: Optional[str] = None
    city: Optional[str] = None
    province: Optional[str] = "ON"
    postal_code: Optional[str] = None
    email: Optional[str] = None
    hire_date: Optional[date] = None
    end_date: Optional[date] = None
    pay_rate: float = 0                # hourly rate
    default_hours: Optional[float] = 80
    vacation_pct: float = 0            # 4 = 4% vacation pay on every cheque
    td1_federal: Optional[float] = None
    td1_ontario: Optional[float] = None
    extra_tax: float = 0
    cpp_exempt: bool = False
    ei_exempt: bool = False
    pay_method: str = "cheque"         # cheque | direct_deposit
    bank_institution: Optional[str] = None
    bank_transit: Optional[str] = None
    bank_account: Optional[str] = None  # left out on an edit = unchanged
    is_active: bool = True


def _digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _sin_ok(sin: str) -> bool:
    """A SIN passes the Luhn check (CRA's own check digit)."""
    if len(sin) != 9:
        return False
    total = 0
    for i, ch in enumerate(sin):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def _out(e: Employee) -> dict:
    sin = decrypt_value(e.sin_enc) if e.sin_enc else ""
    acct = decrypt_value(e.bank_account_enc) if e.bank_account_enc else ""
    return {
        "id": e.id, "first_name": e.first_name, "last_name": e.last_name,
        "sin": sin, "sin_display": f"{sin[:3]} {sin[3:6]} {sin[6:]}" if len(sin) == 9 else "",
        "birth_date": e.birth_date.isoformat() if e.birth_date else None,
        "address1": e.address1, "address2": e.address2, "city": e.city, "province": e.state or "ON",
        "postal_code": e.zip, "email": e.email,
        "hire_date": e.hire_date.isoformat() if e.hire_date else None,
        "end_date": e.end_date.isoformat() if e.end_date else None,
        "pay_rate": float(e.pay_rate or 0), "default_hours": float(e.default_hours) if e.default_hours is not None else None,
        "vacation_pct": float(e.vacation_pct or 0),
        "td1_federal": float(e.td1_federal) if e.td1_federal is not None else None,
        "td1_ontario": float(e.td1_ontario) if e.td1_ontario is not None else None,
        "extra_tax": float(e.extra_withholding or 0),
        "cpp_exempt": bool(e.cpp_exempt), "ei_exempt": bool(e.ei_exempt),
        "pay_method": e.pay_method or "cheque", "bank_institution": e.bank_institution,
        "bank_transit": e.bank_transit, "bank_account_last4": acct[-4:] if acct else "",
        "is_active": bool(e.is_active),
    }


def _apply(e: Employee, data: EmployeeIn, creating: bool):
    if not data.first_name.strip() or not data.last_name.strip():
        raise HTTPException(status_code=400, detail="Enter the employee's first and last name.")
    if data.sin is not None or creating:
        sin = _digits(data.sin)
        if not sin:
            raise HTTPException(status_code=400, detail="Enter the employee's SIN (9 digits).")
        if not _sin_ok(sin):
            raise HTTPException(status_code=400, detail="That SIN isn't valid: check the 9 digits.")
        e.sin_enc = encrypt_value(sin)
        e.ssn_last_four = None
    if creating or data.birth_date is not None:
        if data.birth_date is None:
            raise HTTPException(status_code=400, detail="Enter the employee's date of birth.")
        e.birth_date = data.birth_date
    if not (data.address1 or "").strip() or not (data.city or "").strip() or not (data.postal_code or "").strip():
        raise HTTPException(status_code=400, detail="Enter the employee's address: street, city and postal code.")
    if data.vacation_pct < 0 or data.vacation_pct > 20:
        raise HTTPException(status_code=400, detail="Vacation pay is a percentage, like 4.")
    if data.pay_method not in ("cheque", "direct_deposit"):
        raise HTTPException(status_code=400, detail="Pay by cheque or direct deposit.")
    if data.pay_method == "direct_deposit":
        inst, transit = _digits(data.bank_institution), _digits(data.bank_transit)
        acct = _digits(data.bank_account) if data.bank_account is not None else None
        if len(inst) != 3 or len(transit) != 5 or (acct is not None and not 5 <= len(acct) <= 12) \
                or (acct is None and not e.bank_account_enc):
            raise HTTPException(status_code=400, detail=(
                "For direct deposit enter the bank's institution number (3 digits), "
                "transit number (5 digits) and account number."))
        e.bank_institution, e.bank_transit = inst, transit
        if acct is not None:
            e.bank_account_enc = encrypt_value(acct)
    e.first_name, e.last_name = data.first_name.strip(), data.last_name.strip()
    e.address1, e.address2, e.city = data.address1, data.address2, data.city
    e.state, e.zip, e.email = (data.province or "ON").upper()[:2], data.postal_code, data.email
    e.hire_date, e.end_date = data.hire_date, data.end_date
    e.pay_rate = D(str(data.pay_rate))
    e.default_hours = D(str(data.default_hours)) if data.default_hours is not None else None
    e.vacation_pct = D(str(data.vacation_pct))
    e.td1_federal = D(str(data.td1_federal)) if data.td1_federal is not None else None
    e.td1_ontario = D(str(data.td1_ontario)) if data.td1_ontario is not None else None
    e.extra_withholding = D(str(data.extra_tax or 0))
    e.cpp_exempt, e.ei_exempt = data.cpp_exempt, data.ei_exempt
    e.pay_method = data.pay_method
    e.work_state = None
    e.is_active = data.is_active


@router.get("/employees")
def list_employees(db: Session = Depends(get_db)):
    return [_out(e) for e in db.query(Employee).order_by(Employee.last_name, Employee.first_name).all()]


@router.post("/employees", status_code=201)
def create_employee(data: EmployeeIn, db: Session = Depends(get_db)):
    e = Employee(first_name=data.first_name, last_name=data.last_name)
    _apply(e, data, True)
    db.add(e)
    db.commit()
    db.refresh(e)
    return _out(e)


@router.put("/employees/{employee_id}")
def update_employee(employee_id: int, data: EmployeeIn, db: Session = Depends(get_db)):
    e = db.get(Employee, employee_id)
    if e is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    _apply(e, data, False)
    db.commit()
    db.refresh(e)
    return _out(e)


# ── pay runs ───────────────────────────────────────────────────────────────

class RunLine(BaseModel):
    employee_id: int
    hours: Optional[float] = None
    rate: Optional[float] = None


class RunIn(BaseModel):
    period_start: date
    period_end: date
    pay_date: date
    lines: list[RunLine]


def _stub_out(db, s: PayStub, run: PayRun) -> dict:
    e = db.get(Employee, s.employee_id)
    tax = D(s.federal_tax or 0) + D(s.state_tax or 0)
    out = {
        "id": s.id, "employee_id": s.employee_id, "employee": f"{e.first_name} {e.last_name}" if e else "",
        "pay_method": (e.pay_method if e else "cheque"),
        "hours": float(s.hours or 0), "rate": float(s.rate or 0),
        "wages": float(D(s.gross_pay or 0) - D(s.vacation_pay or 0)), "vacation_pay": float(s.vacation_pay or 0),
        "gross": float(s.gross_pay or 0), "cpp": float(s.cpp or 0), "cpp2": float(s.cpp2 or 0),
        "ei": float(s.ei or 0), "income_tax": float(tax), "net": float(s.net_pay or 0),
        "employer_cpp": float(s.employer_cpp or 0), "employer_cpp2": float(s.employer_cpp2 or 0),
        "employer_ei": float(s.employer_ei or 0), "wsib": float(s.wsib or 0),
    }
    if run.status == PayRunStatus.PROCESSED:
        y = runs.ytd_for(db, s.employee_id, run.pay_date)
    else:
        y = runs.ytd_for(db, s.employee_id, run.pay_date, exclude_run_id=run.id)
        for k, v in (("gross", s.gross_pay), ("vacation_pay", s.vacation_pay), ("cpp", s.cpp), ("cpp2", s.cpp2),
                     ("ei", s.ei), ("income_tax", tax), ("net", s.net_pay), ("hours", s.hours)):
            y[k] += D(v or 0)
    out["ytd"] = {k: float(v) for k, v in y.items()}
    return out


def _run_out(db, run: PayRun) -> dict:
    return {
        "id": run.id, "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
        "pay_date": run.pay_date.isoformat(), "status": run.status.value,
        "total_gross": float(run.total_gross or 0), "total_net": float(run.total_net or 0),
        "total_withheld": float(run.total_taxes or 0), "total_employer": float(run.total_employer_taxes or 0),
        "transaction_id": run.transaction_id,
        "stubs": [_stub_out(db, s, run) for s in run.stubs],
    }


def _run(db, run_id) -> PayRun:
    run = db.get(PayRun, run_id)
    if run is None or run.region != runs.REGION:
        raise HTTPException(status_code=404, detail="Pay run not found")
    return run


@router.get("/runs")
def list_runs(db: Session = Depends(get_db)):
    rs = db.query(PayRun).filter(PayRun.region == runs.REGION).order_by(PayRun.pay_date.desc(), PayRun.id.desc()).all()
    return [{k: v for k, v in _run_out(db, r).items() if k != "stubs"} | {"employees": len(r.stubs)} for r in rs]


@router.post("/runs", status_code=201)
def create_run(data: RunIn, db: Session = Depends(get_db)):
    if data.period_end < data.period_start:
        raise HTTPException(status_code=400, detail="The pay period ends before it starts.")
    run = runs.create_run(db, data.period_start, data.period_end, data.pay_date, [l.model_dump() for l in data.lines])
    db.commit()
    return _run_out(db, run)


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    return _run_out(db, _run(db, run_id))


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db)):
    run = _run(db, run_id)
    if run.status != PayRunStatus.DRAFT:
        raise HTTPException(status_code=400, detail="Only a draft pay run can be deleted; void a processed one.")
    for s in list(run.stubs):
        db.delete(s)
    db.delete(run)
    db.commit()
    return {"deleted": True}


class ProcessIn(BaseModel):
    bank_account_id: int


@router.post("/runs/{run_id}/process")
def process_run(run_id: int, data: ProcessIn, db: Session = Depends(get_db)):
    run = runs.process_run(db, _run(db, run_id), data.bank_account_id)
    db.commit()
    return _run_out(db, run)


@router.post("/runs/{run_id}/void")
def void_run(run_id: int, db: Session = Depends(get_db)):
    run = runs.void_run(db, _run(db, run_id))
    db.commit()
    return _run_out(db, run)


@router.get("/runs/{run_id}/stubs/{stub_id}/pdf")
def stub_pdf(run_id: int, stub_id: int, db: Session = Depends(get_db)):
    from app.services.pdf_service import _render
    from app.services.settings_service import get_all_settings

    run = _run(db, run_id)
    stub = next((s for s in run.stubs if s.id == stub_id), None)
    if stub is None:
        raise HTTPException(status_code=404, detail="Pay stub not found")
    e = db.get(Employee, stub.employee_id)
    html = _render("pay_stub_ca.html", get_all_settings(db), run=run, s=_stub_out(db, stub, run), e=e)
    from app.services.pdf_service import render_pdf

    pdf = render_pdf(html)
    name = f"Pay stub {e.first_name} {e.last_name} {run.pay_date:%Y-%m-%d}.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"'})


# ── reports ────────────────────────────────────────────────────────────────

def _stubs_between(db, start: date, end: date):
    return (db.query(PayStub, PayRun).join(PayRun, PayRun.id == PayStub.pay_run_id)
            .filter(PayRun.region == runs.REGION, PayRun.status == PayRunStatus.PROCESSED,
                    PayRun.pay_date >= start, PayRun.pay_date <= end).all())


def _month_end(y, m):
    return date(y + (m == 12), m % 12 + 1, 1).fromordinal(date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1)


@router.get("/reports/pd7a")
def pd7a(year: int, month: int = Query(..., ge=1, le=12), db: Session = Depends(get_db)):
    """The month's remittance to the Receiver General (form PD7A): pays
    dated in the month. Regular remitter: due the 15th of the next month."""
    start, end = date(year, month, 1), _month_end(year, month)
    rows = _stubs_between(db, start, end)
    s = lambda f: sum((D(getattr(st, f) or 0) for st, _ in rows), D(0))
    cpp = s("cpp") + s("employer_cpp") + s("cpp2") + s("employer_cpp2")
    ei = s("ei") + s("employer_ei")
    tax = s("federal_tax") + s("state_tax")
    last_pay = max((r.pay_date for _, r in rows), default=None)
    employees_last = len({st.employee_id for st, r in rows if r.pay_date == last_pay}) if last_pay else 0
    due = date(year + (month == 12), month % 12 + 1, 15)
    return {
        "year": year, "month": month, "due_date": due.isoformat(),
        "gross_payroll": float(s("gross_pay")), "employees_last_pay": employees_last,
        "cpp": float(cpp), "ei": float(ei), "income_tax": float(tax),
        "total": float(cpp + ei + tax), "pays": len({r.id for _, r in rows}),
    }


@router.get("/reports/wsib")
def wsib(year: int, quarter: int = Query(..., ge=1, le=4), db: Session = Depends(get_db)):
    start = date(year, 3 * quarter - 2, 1)
    end = _month_end(year, 3 * quarter)
    rows = _stubs_between(db, start, end)
    insurable = sum((D(st.gross_pay or 0) for st, _ in rows), D(0))
    premium = sum((D(st.wsib or 0) for st, _ in rows), D(0))
    return {"year": year, "quarter": quarter, "from": start.isoformat(), "to": end.isoformat(),
            "rate": float(runs.wsib_rate(db)), "insurable_earnings": float(insurable), "premium": float(premium)}


def _t4_rows(db, year: int):
    rows = _stubs_between(db, date(year, 1, 1), date(year, 12, 31))
    by = {}
    for st, _ in rows:
        b = by.setdefault(st.employee_id, {k: D(0) for k in ("14", "16", "16A", "18", "22", "24", "26")})
        b["14"] += D(st.gross_pay or 0)
        b["16"] += D(st.cpp or 0)
        b["16A"] += D(st.cpp2 or 0)
        b["18"] += D(st.ei or 0)
        b["22"] += D(st.federal_tax or 0) + D(st.state_tax or 0)
        if D(st.ei or 0) > 0:
            b["24"] += D(st.gross_pay or 0)
        if D(st.cpp or 0) > 0 or D(st.cpp2 or 0) > 0:
            b["26"] += D(st.gross_pay or 0)
    t = None
    try:
        from app.services.payroll_ca import table_for

        t = table_for(date(year, 12, 31))
    except Exception:
        pass
    out = []
    for emp_id, b in by.items():
        if t is not None:  # boxes 24 and 26 stop at the year's maximums
            b["24"] = min(b["24"], t["ei_max_insurable"])
            b["26"] = min(b["26"], t["yampe"])
        e = db.get(Employee, emp_id)
        sin = decrypt_value(e.sin_enc) if e.sin_enc else ""
        out.append({"employee_id": emp_id, "name": f"{e.last_name}, {e.first_name}", "sin": sin,
                    "address": ", ".join(x for x in (e.address1, e.address2, e.city, e.state, e.zip) if x),
                    "province_of_employment": "ON", **{f"box_{k}": float(v) for k, v in b.items()}})
    return sorted(out, key=lambda r: r["name"])


@router.get("/reports/t4")
def t4(year: int, db: Session = Depends(get_db)):
    slips = _t4_rows(db, year)
    rows = _stubs_between(db, date(year, 1, 1), date(year, 12, 31))
    summary = {
        "slips": len(slips),
        "box_14": sum(s["box_14"] for s in slips), "box_16_employee_cpp": sum(s["box_16"] for s in slips),
        "box_16A_employee_cpp2": sum(s["box_16A"] for s in slips), "box_18_employee_ei": sum(s["box_18"] for s in slips),
        "box_22_income_tax": sum(s["box_22"] for s in slips),
        "employer_cpp": float(sum((D(st.employer_cpp or 0) for st, _ in rows), D(0))),
        "employer_cpp2": float(sum((D(st.employer_cpp2 or 0) for st, _ in rows), D(0))),
        "employer_ei": float(sum((D(st.employer_ei or 0) for st, _ in rows), D(0))),
    }
    summary["total_deductions"] = round(summary["box_16_employee_cpp"] + summary["box_16A_employee_cpp2"]
        + summary["box_18_employee_ei"] + summary["box_22_income_tax"] + summary["employer_cpp"]
        + summary["employer_cpp2"] + summary["employer_ei"], 2)
    return {"year": year, "slips": slips, "summary": summary}


@router.get("/reports/t4.csv")
def t4_csv(year: int, db: Session = Depends(get_db)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "SIN", "Address", "Province of employment", "Box 14 Employment income",
                "Box 16 CPP", "Box 16A CPP2", "Box 18 EI", "Box 22 Income tax", "Box 24 EI insurable earnings",
                "Box 26 CPP pensionable earnings"])
    for s in _t4_rows(db, year):
        w.writerow([s["name"], s["sin"], s["address"], s["province_of_employment"], f"{s['box_14']:.2f}",
                    f"{s['box_16']:.2f}", f"{s['box_16A']:.2f}", f"{s['box_18']:.2f}", f"{s['box_22']:.2f}",
                    f"{s['box_24']:.2f}", f"{s['box_26']:.2f}"])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="T4 {year}.csv"'})
