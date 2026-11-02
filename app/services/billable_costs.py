# ============================================================================
# bsq: billable costs onto invoices, as QuickBooks' "Choose Billable Time and
# Costs". A cost marked billable (a cheque, card charge, bill or job cost
# line with is_billable and a job) waits, unbilled, until an invoice for that
# job's customer takes it on a line. The cost's journal line then carries
# billed_invoice_line_id, so it is offered once only; voiding the invoice, or
# taking the line off it, frees the cost again.
# ============================================================================

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.contacts import Customer, Vendor
from app.models.invoices import InvoiceLine
from app.models.jobs import Job
from app.models.transactions import Transaction, TransactionLine

# where a cost can come from, and what to call it
SOURCE_LABELS = {
    "expense": "Cheque",
    "cc_charge": "Card",
    "bill": "Bill",
    "job_cost": "Job cost",
}


def _voided(db: Session, txns) -> set[int]:
    """Ids of the transactions a `<type>_void` entry reverses (it names the
    transaction, or the document behind it)."""
    out = set()
    if not txns:
        return out
    keys = {}
    for t in txns:
        void_type = f"{t.source_type}_void"
        keys.setdefault(void_type, set()).update({t.id, t.source_id} - {None})
    reversed_pairs = set()
    for void_type, ids in keys.items():
        rows = (
            db.query(Transaction.source_id)
            .filter(Transaction.source_type == void_type, Transaction.source_id.in_(ids))
            .all()
        )
        reversed_pairs.update((void_type, r[0]) for r in rows)
    for t in txns:
        void_type = f"{t.source_type}_void"
        if (void_type, t.id) in reversed_pairs or (
            t.source_id is not None and t.source_type in ("bill", "job_cost")
            and (void_type, t.source_id) in reversed_pairs
        ):
            out.add(t.id)
    return out


def _payee(db: Session, txn: Transaction) -> str:
    """Who the cost was paid to."""
    if txn.source_type == "expense" and txn.source_id:
        v = db.get(Vendor, txn.source_id)
        if v:
            return v.name
    if txn.source_type == "bill" and txn.source_id:
        from app.models.bills import Bill

        b = db.get(Bill, txn.source_id)
        if b and b.vendor:
            return b.vendor.name
    desc = txn.description or ""
    for prefix in ("Expense: ", "CC Charge: ", "Bill "):
        if desc.startswith(prefix):
            return desc[len(prefix):].strip()
    return desc


def unbilled_costs(db: Session, customer_id: int, job_id: int | None = None):
    """The customer's billable costs not yet on an invoice, oldest first."""
    jobs = db.query(Job).filter(Job.customer_id == customer_id).all()
    job_ids = [j.id for j in jobs if job_id is None or j.id == job_id]
    if not job_ids:
        return []
    job_names = {j.id: j.name for j in jobs}
    customer = db.get(Customer, customer_id)
    rows = (
        db.query(TransactionLine, Transaction)
        .join(Transaction, Transaction.id == TransactionLine.transaction_id)
        .filter(
            TransactionLine.is_billable.is_(True),
            TransactionLine.billed_invoice_line_id.is_(None),
            TransactionLine.debit > 0,
            TransactionLine.job_id.in_(job_ids),
            ~Transaction.source_type.like("%\\_void", escape="\\"),
        )
        .order_by(Transaction.date, TransactionLine.id)
        .all()
    )
    voided = _voided(db, list({t.id: t for _, t in rows}.values()))
    out = []
    for ln, txn in rows:
        if txn.id in voided:
            continue
        job = job_names.get(ln.job_id, "")
        out.append(
            {
                "id": ln.id,
                "date": txn.date.isoformat(),
                "payee": _payee(db, txn),
                "memo": ln.description or "",
                "amount": float(ln.debit),
                "account_name": ln.account.name if ln.account else "",
                "source": SOURCE_LABELS.get(txn.source_type, txn.source_type or ""),
                "reference": txn.reference or "",
                "job_id": ln.job_id,
                "job_name": job if customer is None or job.lower() != (customer.name or "").lower() else "",
            }
        )
    return out


def release_invoice(db: Session, invoice_id: int) -> int:
    """Free every cost billed on the invoice (it is voided, or its lines are
    about to be written again)."""
    line_ids = [
        r[0] for r in db.query(InvoiceLine.id).filter(InvoiceLine.invoice_id == invoice_id).all()
    ]
    if not line_ids:
        return 0
    n = (
        db.query(TransactionLine)
        .filter(TransactionLine.billed_invoice_line_id.in_(line_ids))
        .update({TransactionLine.billed_invoice_line_id: None}, synchronize_session=False)
    )
    return n


def bill_costs(db: Session, customer_id: int, pairs) -> None:
    """Mark each cost billed on its invoice line. `pairs` is
    [(invoice_line, cost_line_id)]. A cost must be billable, unbilled, and
    on one of this customer's jobs."""
    for inv_line, cost_id in pairs:
        cost = db.get(TransactionLine, cost_id)
        job = db.get(Job, cost.job_id) if cost is not None and cost.job_id else None
        if cost is None or not cost.is_billable or cost.debit <= 0:
            raise HTTPException(status_code=400, detail="That cost isn't marked billable.")
        if job is None or job.customer_id != customer_id:
            raise HTTPException(
                status_code=400,
                detail="That billable cost is for another customer's job.",
            )
        if cost.billed_invoice_line_id is not None and cost.billed_invoice_line_id != inv_line.id:
            raise HTTPException(
                status_code=400,
                detail="That billable cost is already on another invoice.",
            )
        cost.billed_invoice_line_id = inv_line.id


def billed_cost_ids(db: Session, invoice_line_ids) -> dict[int, int]:
    """{invoice line id: the cost it bills}."""
    ids = list(invoice_line_ids)
    if not ids:
        return {}
    rows = (
        db.query(TransactionLine.billed_invoice_line_id, TransactionLine.id)
        .filter(TransactionLine.billed_invoice_line_id.in_(ids))
        .all()
    )
    return {a: b for a, b in rows}


def keep_billed_links(db: Session, old_lines, new_lines) -> None:
    """A cost document re-posted by an edit keeps its billed lines' links:
    each billed old line is matched to a new line with the same account,
    job and amount. One that has no match was changed after it was billed,
    so the edit is refused."""
    billed = [ln for ln in old_lines if ln.billed_invoice_line_id is not None]
    taken = set()
    for old in billed:
        match = next(
            (
                n for n in new_lines
                if id(n) not in taken
                and n.account_id == old.account_id
                and n.job_id == old.job_id
                and Decimal(str(n.debit)) == Decimal(str(old.debit))
            ),
            None,
        )
        if match is None:
            inv_line = db.get(InvoiceLine, old.billed_invoice_line_id)
            number = inv_line.invoice.invoice_number if inv_line and inv_line.invoice else "?"
            raise HTTPException(
                status_code=400,
                detail=(
                    f"A line of {old.debit} has already been billed on invoice {number}. "
                    "Take it off that invoice first, or leave its account, job and amount as they are."
                ),
            )
        taken.add(id(match))
        match.is_billable = True
        match.billed_invoice_line_id = old.billed_invoice_line_id


def billed_snapshot(db: Session, transaction_id: int):
    """The transaction's billed cost lines, kept across a re-post."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            account_id=ln.account_id, job_id=ln.job_id, debit=ln.debit,
            billed_invoice_line_id=ln.billed_invoice_line_id,
        )
        for ln in db.query(TransactionLine)
        .filter(
            TransactionLine.transaction_id == transaction_id,
            TransactionLine.billed_invoice_line_id.isnot(None),
        )
        .all()
    ]


def restore_billed(db: Session, transaction_id: int, snapshot) -> None:
    """After a re-post, the billed lines' links go back on the matching new
    lines (refused if one no longer matches)."""
    if not snapshot:
        return
    db.flush()
    new_lines = (
        db.query(TransactionLine)
        .filter(TransactionLine.transaction_id == transaction_id, TransactionLine.debit > 0)
        .order_by(TransactionLine.id)
        .all()
    )
    keep_billed_links(db, snapshot, new_lines)
