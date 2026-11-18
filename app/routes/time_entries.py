# ============================================================================
# Time entries — daily time tracking with a submit / approve workflow
# Tier 1.4: approved entries feed pay runs (see routes/payroll.py).
# ============================================================================

from datetime import datetime, timezone, date, timedelta
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from app.schemas.common import StrictModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.payroll import Employee
from app.models.time_entries import TimeEntry, TimeEntryStatus
from app.schemas.time_entries import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimeEntryResponse,
    TimeEntryApprove,
)
from app.services.overtime import classify_period
from app.services.safe_errors import safe_message

router = APIRouter(prefix="/api/time-entries", tags=["time-entries"])


# ── bsq: weekly timesheet, as QuickBooks' Timesheet ───────────────────────
# One employee, one week (Monday to Sunday). Each row is a Customer:Job, a
# service item, a payroll item, notes and a Billable tick, with hours on
# each day; each day's hours are kept as one time entry. Saving the week
# writes it again, except entries a pay run has paid, an invoice has billed
# or a job cost has posted: those must stay as they are.

PAY_TYPES = {
    "regular": "hours_regular",
    "overtime": "hours_overtime",
    "doubletime": "hours_doubletime",
}


class TimesheetRow(StrictModel):
    job_id: Optional[int] = None
    customer_id: Optional[int] = None   # a customer alone: its own job
    service_item_id: Optional[int] = None
    pay_type: str = "regular"
    notes: Optional[str] = None
    billable: bool = False
    hours: list[float]                  # Monday .. Sunday


class TimesheetIn(StrictModel):
    employee_id: int
    week_start: date
    rows: list[TimesheetRow]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _entry_hours(te: TimeEntry) -> tuple[str, Decimal]:
    for pay_type, field in PAY_TYPES.items():
        h = Decimal(str(getattr(te, field) or 0))
        if h:
            return pay_type, h
    return "regular", Decimal("0")


def _lock_reason(db: Session, te: TimeEntry) -> Optional[str]:
    if te.pay_run_id is not None:
        return "paid on a pay run"
    if te.billed_invoice_line_id is not None:
        from app.models.invoices import InvoiceLine

        line = db.get(InvoiceLine, te.billed_invoice_line_id)
        number = line.invoice.invoice_number if line is not None and line.invoice else "?"
        return f"billed on invoice {number}"
    if te.job_cost_id is not None:
        return "posted to its job as a job cost"
    return None


def _week_entries(db: Session, employee_id: int, start: date):
    return (
        db.query(TimeEntry)
        .filter(
            TimeEntry.employee_id == employee_id,
            TimeEntry.date >= start,
            TimeEntry.date <= start + timedelta(days=6),
            TimeEntry.source.is_(None),   # QuickBooks history is not timesheet time
        )
        .order_by(TimeEntry.id)
        .all()
    )


def _key(job_id, item_id, pay_type, notes, billable):
    return (job_id or None, item_id or None, pay_type, (notes or "").strip(), bool(billable))


def _timesheet(db: Session, employee_id: int, start: date) -> dict:
    rows: list[dict] = []
    for te in _week_entries(db, employee_id, start):
        pay_type, h = _entry_hours(te)
        k = _key(te.job_id, te.service_item_id, pay_type, te.notes, te.is_billable)
        day = (te.date - start).days
        row = next((r for r in rows if r["_key"] == k and r["hours"][day] == 0 and not r["entry_ids"][day]), None)
        if row is None:
            row = {
                "_key": k, "job_id": te.job_id, "service_item_id": te.service_item_id,
                "pay_type": pay_type, "notes": (te.notes or "").strip(), "billable": bool(te.is_billable),
                "hours": [0.0] * 7, "entry_ids": [None] * 7, "locked": [None] * 7,
            }
            rows.append(row)
        row["hours"][day] = float(h)
        row["entry_ids"][day] = te.id
        row["locked"][day] = _lock_reason(db, te)
    for r in rows:
        r.pop("_key")
    return {
        "employee_id": employee_id,
        "week_start": start.isoformat(),
        "days": [(start + timedelta(days=i)).isoformat() for i in range(7)],
        "rows": rows,
    }


@router.get("/week")
def get_timesheet(
    employee_id: int = Query(...),
    week_of: date = Query(...),
    db: Session = Depends(get_db),
):
    """The employee's timesheet for the week (Monday to Sunday) holding week_of."""
    if not db.get(Employee, employee_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    return _timesheet(db, employee_id, _monday(week_of))


@router.put("/week")
def save_timesheet(data: TimesheetIn, db: Session = Depends(get_db)):
    """Save the employee's week as the rows say."""
    from app.models.items import Item
    from app.services.purchase_posting import resolve_job

    if not db.get(Employee, data.employee_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    start = data.week_start
    if start.weekday() != 0:
        raise HTTPException(status_code=400, detail="A timesheet week starts on a Monday.")

    cells = []   # (key, day, hours)
    day_totals = [Decimal("0")] * 7
    for n, row in enumerate(data.rows, start=1):
        if len(row.hours) != 7:
            raise HTTPException(status_code=400, detail="Each row needs hours for the 7 days.")
        if row.pay_type not in PAY_TYPES:
            raise HTTPException(status_code=400, detail="Choose a payroll item for each row.")
        hours = [Decimal(str(h or 0)).quantize(Decimal("0.01")) for h in row.hours]
        if any(h < 0 for h in hours):
            raise HTTPException(status_code=400, detail=f"Row {n}: hours can't be negative.")
        if not any(hours):
            continue   # an empty row is no time
        job_id = resolve_job(db, row.job_id, row.customer_id)
        if row.billable and not job_id:
            raise HTTPException(
                status_code=400,
                detail=f"Row {n} is billable: choose the customer or job it is billed to.",
            )
        if row.service_item_id and not db.get(Item, row.service_item_id):
            raise HTTPException(status_code=400, detail=f"Row {n}: that service item doesn't exist.")
        k = _key(job_id, row.service_item_id, row.pay_type, row.notes, row.billable)
        for day, h in enumerate(hours):
            if h:
                cells.append((k, day, h))
                day_totals[day] += h
    for day, total in enumerate(day_totals):
        if total > 24:
            d = start + timedelta(days=day)
            raise HTTPException(
                status_code=400, detail=f"{d:%a %b} {d.day} adds up to {total} hours: more than a day."
            )

    existing = _week_entries(db, data.employee_id, start)
    for te in existing:
        reason = _lock_reason(db, te)
        if reason is None:
            continue
        pay_type, h = _entry_hours(te)
        k = _key(te.job_id, te.service_item_id, pay_type, te.notes, te.is_billable)
        day = (te.date - start).days
        match = next((c for c in cells if c == (k, day, h)), None)
        if match is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The {h} hours on {te.date:%a %b} {te.date.day} were {reason}, "
                    "so they can't be changed here."
                ),
            )
        cells.remove(match)
    for te in existing:
        if _lock_reason(db, te) is None:
            db.delete(te)
    db.flush()
    for (job_id, item_id, pay_type, notes, billable), day, h in cells:
        te = TimeEntry(
            employee_id=data.employee_id,
            date=start + timedelta(days=day),
            job_id=job_id,
            service_item_id=item_id,
            notes=notes or None,
            is_billable=billable,
            hours_regular=0, hours_overtime=0, hours_doubletime=0,
        )
        setattr(te, PAY_TYPES[pay_type], h)
        db.add(te)
    db.commit()
    return _timesheet(db, data.employee_id, start)


def _resp(entry: TimeEntry) -> TimeEntryResponse:
    r = TimeEntryResponse.model_validate(entry)
    if entry.employee:
        r.employee_name = entry.employee.full_name
    if entry.job_id and entry.job:
        r.job_name = entry.job.full_name
    if entry.cost_code_id and entry.cost_code:
        r.cost_code_label = entry.cost_code.label
    return r


class PostToJobRequest(StrictModel):
    ids: list[int]


@router.post("/post-to-job")
def post_entries_to_job(data: PostToJobRequest, db: Session = Depends(get_db)):
    """Post several approved time entries to their jobs as labor cost (one
    Job Cost Entry each). Returns per-entry results; nothing is rolled
    back for a single failure, so the caller sees exactly what posted."""
    from app.services.job_costing import post_time_entry_to_job

    results = []
    for entry_id in data.ids:
        entry = db.get(TimeEntry, entry_id)
        if not entry:
            results.append({"id": entry_id, "ok": False, "error": "not found"})
            continue
        try:
            jc = post_time_entry_to_job(db, entry)
            db.commit()
            results.append(
                {
                    "id": entry_id,
                    "ok": True,
                    "job_cost_id": jc.id,
                    "number": jc.number,
                    "total": float(jc.total),
                }
            )
        except ValueError as exc:
            db.rollback()
            results.append(
                {
                    "id": entry_id,
                    "ok": False,
                    "error": safe_message(exc, "post time entry to job"),
                }
            )
    return {"results": results, "posted": sum(1 for r in results if r["ok"])}


@router.get("", response_model=list[TimeEntryResponse])
def list_time_entries(
    employee_id: int = Query(default=None),
    start: date = Query(default=None),
    end: date = Query(default=None),
    status: str = Query(default=None),
    db: Session = Depends(get_db),
):
    q = db.query(TimeEntry).options(joinedload(TimeEntry.employee))
    if employee_id:
        q = q.filter(TimeEntry.employee_id == employee_id)
    if start:
        q = q.filter(TimeEntry.date >= start)
    if end:
        q = q.filter(TimeEntry.date <= end)
    if status:
        try:
            q = q.filter(TimeEntry.status == TimeEntryStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    return [_resp(e) for e in q.order_by(TimeEntry.date.desc()).all()]


@router.post("", response_model=TimeEntryResponse, status_code=201)
def create_time_entry(data: TimeEntryCreate, db: Session = Depends(get_db)):
    if not db.query(Employee).filter(Employee.id == data.employee_id).first():
        raise HTTPException(status_code=404, detail="Employee not found")
    entry = TimeEntry(**data.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.put("/{entry_id}", response_model=TimeEntryResponse)
def update_time_entry(
    entry_id: int, data: TimeEntryUpdate, db: Session = Depends(get_db)
):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry.pay_run_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Time entry is locked to a pay run and cannot be edited",
        )
    if entry.billed_invoice_line_id is not None:
        raise HTTPException(
            status_code=400,
            detail="This time is billed on an invoice. Take it off the invoice first.",
        )
    fields = data.model_dump(exclude_unset=True)
    if "status" in fields:
        try:
            fields["status"] = TimeEntryStatus(fields["status"])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")
    for key, val in fields.items():
        setattr(entry, key, val)
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.delete("/{entry_id}")
def delete_time_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    if entry.pay_run_id is not None:
        raise HTTPException(
            status_code=400,
            detail="Time entry is locked to a pay run and cannot be deleted",
        )
    if entry.billed_invoice_line_id is not None:
        raise HTTPException(
            status_code=400,
            detail="This time is billed on an invoice. Take it off the invoice first.",
        )
    db.delete(entry)
    db.commit()
    return {"status": "deleted", "id": entry_id}


@router.post("/{entry_id}/submit", response_model=TimeEntryResponse)
def submit_time_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    entry.status = TimeEntryStatus.SUBMITTED
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.post("/{entry_id}/approve", response_model=TimeEntryResponse)
def approve_time_entry(
    entry_id: int, data: TimeEntryApprove, db: Session = Depends(get_db)
):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    entry.status = TimeEntryStatus.APPROVED
    entry.approved_by = data.approved_by
    entry.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return _resp(entry)


@router.post("/{entry_id}/post-to-job")
def post_entry_to_job(entry_id: int, db: Session = Depends(get_db)):
    """Post one approved time entry to its job as labor cost at the
    employee's loaded rate, with burden as its own line."""
    from app.services.job_costing import post_time_entry_to_job

    entry = db.get(TimeEntry, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    try:
        jc = post_time_entry_to_job(db, entry)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=safe_message(exc, "post time entry to job")
        )
    db.commit()
    return {
        "id": entry.id,
        "job_cost_id": jc.id,
        "number": jc.number,
        "total": float(jc.total),
    }


@router.post("/{entry_id}/reject", response_model=TimeEntryResponse)
def reject_time_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(TimeEntry).filter(TimeEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Time entry not found")
    # A submitted entry may already be posted to its job; rejecting it has
    # to take that labor cost back off the job, not leave it behind.
    if entry.job_cost_id:
        from app.models.job_costing import JobCost
        from app.services.closing_date import check_closing_date
        from app.services.job_costing import void_job_cost

        jc = db.get(JobCost, entry.job_cost_id)
        if jc is not None and jc.status != "void":
            check_closing_date(db, jc.date)  # the reversal posts on that date
            try:
                void_job_cost(db, jc)
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail=safe_message(exc, "void job cost")
                )
        entry.job_cost_id = None
    entry.status = TimeEntryStatus.REJECTED
    db.commit()
    db.refresh(entry)
    return _resp(entry)


class ClassifyRequest(StrictModel):
    weeks: list[list[float]]  # each inner list = daily hours for one workweek
    state: str = "WA"


@router.post("/classify")
def classify_hours(data: ClassifyRequest):
    """Run the overtime engine over raw daily hours (FLSA + state overrides)."""
    weeks = [[Decimal(str(h)) for h in week] for week in data.weeks]
    result = classify_period(weeks, data.state)
    return {k: float(v) for k, v in result.items()}


@router.get("/summary")
def pay_period_summary(
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: Session = Depends(get_db),
):
    """Hours-by-employee summary for an upcoming pay period.

    The hours are approved entries that haven't been swept into a pay run
    yet (pay_run_id IS NULL) and were entered here — same filter the
    pay-run create flow applies when `use_time_entries=true`; time imported
    from QuickBooks (source set) is history it never pays, so the form
    never shows it (2.23.4 gate, W-11 / NEW-131). `pending_count` / `pending_hours` are the
    entries in the period still waiting for approval (draft or submitted),
    which that flow will not pay. The SPA uses this to pre-fill / preview
    the "Calculate Payroll" form and to warn before a run leaves time unpaid.
    """
    if period_end < period_start:
        raise HTTPException(status_code=400, detail="period_end before period_start")

    rows = (
        db.query(TimeEntry)
        .options(joinedload(TimeEntry.employee))
        .filter(
            TimeEntry.status.in_(
                [
                    TimeEntryStatus.APPROVED,
                    TimeEntryStatus.DRAFT,
                    TimeEntryStatus.SUBMITTED,
                ]
            ),
            TimeEntry.pay_run_id.is_(None),
            TimeEntry.source.is_(None),
            TimeEntry.date >= period_start,
            TimeEntry.date <= period_end,
        )
        .all()
    )

    by_emp: dict[int, dict] = {}
    for te in rows:
        bucket = by_emp.setdefault(
            te.employee_id,
            {
                "employee_id": te.employee_id,
                "employee_name": te.employee.full_name if te.employee else None,
                "regular": Decimal("0"),
                "overtime": Decimal("0"),
                "doubletime": Decimal("0"),
                "entry_count": 0,
                "pending_count": 0,
                "pending_hours": Decimal("0"),
            },
        )
        reg = Decimal(str(te.hours_regular or 0))
        ot = Decimal(str(te.hours_overtime or 0))
        dt = Decimal(str(te.hours_doubletime or 0))
        if te.status != TimeEntryStatus.APPROVED:
            bucket["pending_count"] += 1
            bucket["pending_hours"] += reg + ot + dt
            continue
        bucket["regular"] += reg
        bucket["overtime"] += ot
        bucket["doubletime"] += dt
        bucket["entry_count"] += 1

    return [
        {
            **b,
            "regular": float(b["regular"]),
            "overtime": float(b["overtime"]),
            "doubletime": float(b["doubletime"]),
            "total": float(b["regular"] + b["overtime"] + b["doubletime"]),
            "pending_hours": float(b["pending_hours"]),
        }
        for b in by_emp.values()
    ]
