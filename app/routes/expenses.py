# ============================================================================
# Expenses — record a receipt that's already been paid (card, cash, check)
# DR Expense Account, CR the bank / credit-card account it was paid from.
# One step, no bill to pay later; the Scan Receipt flow lands here.
# ============================================================================

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account
from app.models.contacts import Vendor
from app.models.transactions import Transaction
from app.schemas.expenses import ExpenseCreate, ExpenseResponse
from app.services.accounting import create_journal_entry, reversing_lines
from app.services.bank_posting import assert_not_reconciled, release_statement_links
from app.services.closing_date import check_closing_date
from app.services.purchase_posting import (
    get_itc_account_id,
    is_itc_line,
    itc_amount_of,
    resolve_job,
    split_lines_out,
    split_paid_amount,
    split_purchase_lines,
)

router = APIRouter(prefix="/api/expenses", tags=["expenses"])

SOURCE_TYPE = "expense"
VOID_SOURCE_TYPE = "expense_void"

# Accounts an expense can be paid from: cash on hand or credit extended.
PAID_FROM_TYPES = ("asset", "liability")


def _void_ids(db: Session, txn_ids: list[int]) -> set[int]:
    """Ids of expenses that already have a reversing entry posted."""
    if not txn_ids:
        return set()
    rows = (
        db.query(Transaction.source_id)
        .filter(
            Transaction.source_type == VOID_SOURCE_TYPE,
            Transaction.source_id.in_(txn_ids),
        )
        .all()
    )
    return {r[0] for r in rows}


def _serialize(
    txn: Transaction, db: Session, voided: bool | None = None
) -> ExpenseResponse:
    if voided is None:
        voided = txn.id in _void_ids(db, [txn.id])
    # bsq: the HST line (1350) is not the expense; the amount is what was
    # paid, from the paid-from credit.
    debit_line = next(
        (ln for ln in txn.lines if ln.debit > 0 and not is_itc_line(ln)), None
    )
    # the money out: the bank or card line (a split may carry a credit line
    # of its own, a return, before it)
    credit_line = next(
        (ln for ln in txn.lines if ln.credit > 0 and ln.account is not None and ln.account.bank_kind),
        None,
    ) or next((ln for ln in txn.lines if ln.credit > 0), None)
    vendor = (
        db.query(Vendor).filter(Vendor.id == txn.source_id).first()
        if txn.source_id
        else None
    )
    desc = txn.description or ""
    payee = vendor.name if vendor else desc.removeprefix("Expense: ").strip()
    split = split_lines_out(db, txn)
    return ExpenseResponse(
        id=txn.id,
        date=txn.date,
        payee=payee,
        vendor_id=vendor.id if vendor else None,
        expense_account_name=(
            f"Split ({len(split)} lines)" if len(split) > 1
            else (debit_line.account.name if debit_line and debit_line.account else "")
        ),
        paid_from_account_name=(
            credit_line.account.name if credit_line and credit_line.account else ""
        ),
        paid_from_account_id=credit_line.account_id if credit_line else None,
        class_id=txn.class_id,
        amount=float(credit_line.credit) if credit_line else 0.0,
        tax_amount=float(itc_amount_of(txn)),
        reference=txn.reference or "",
        # a split's lines carry their own descriptions
        memo=(debit_line.description or "") if debit_line and len(split) <= 1 else "",
        status="void" if voided else "recorded",
        lines=split,
    )


@router.get("", response_model=list[ExpenseResponse])
def list_expenses(db: Session = Depends(get_db)):
    txns = (
        db.query(Transaction)
        .filter(Transaction.source_type == SOURCE_TYPE)
        .order_by(Transaction.date.desc(), Transaction.id.desc())
        .all()
    )
    voided = _void_ids(db, [t.id for t in txns])
    return [_serialize(t, db, t.id in voided) for t in txns]


@router.get("/{expense_id}", response_model=ExpenseResponse)
def get_expense(expense_id: int, db: Session = Depends(get_db)):
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == expense_id, Transaction.source_type == SOURCE_TYPE)
        .first()
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return _serialize(txn, db)


@router.post("/{expense_id}/void", response_model=ExpenseResponse)
def void_expense(expense_id: int, db: Session = Depends(get_db)):
    """Reverse a recorded expense — same convention as bills and manual
    entries: the original stays in the ledger, a mirror-image entry
    cancels it, and the expense shows as void. Booked it to the wrong
    account? Void it and enter it again."""
    txn = (
        db.query(Transaction)
        .filter(Transaction.id == expense_id, Transaction.source_type == SOURCE_TYPE)
        .first()
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if txn.id in _void_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="Expense is already void")

    assert_not_reconciled(txn)
    check_closing_date(db, txn.date)

    create_journal_entry(
        db,
        txn.date,
        f"VOID {txn.description or 'Expense'}",
        reversing_lines(txn.lines),
        source_type=VOID_SOURCE_TYPE,
        source_id=txn.id,
        reference=txn.reference or "",
        class_id=txn.class_id,
        job_id=txn.job_id,
    )
    release_statement_links(db, txn)
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db, True)


def _build(db: Session, data: ExpenseCreate):
    """The journal for a cheque / debit / cash / e-transfer purchase:
    (lines, header job, vendor, payee, paid-from account)."""
    amount = Decimal(str(data.amount))
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    paid_from = (
        db.query(Account).filter(Account.id == data.paid_from_account_id).first()
    )
    if paid_from is None:
        raise HTTPException(status_code=404, detail="Paid-from account not found")
    if paid_from.account_type not in PAID_FROM_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Paid-from must be a bank, cash, or credit card account",
        )

    vendor = None
    if data.vendor_id is not None:
        vendor = db.query(Vendor).filter(Vendor.id == data.vendor_id).first()
        if vendor is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

    payee = (data.payee or "").strip() or (vendor.name if vendor else "")
    memo = (data.memo or "").strip()
    line_desc = memo or payee
    paid_line = {
        "account_id": paid_from.id,
        "debit": Decimal("0"),
        "credit": amount,
        "description": line_desc,
    }

    if data.lines:
        # bsq: split over several expense types and customers
        debit_lines, itc = split_purchase_lines(
            db, amount, data.tax_amount, data.lines, line_desc, paid_from.id
        )
        journal_lines = debit_lines
        header_job = None
    else:
        expense_account = (
            db.query(Account).filter(Account.id == data.expense_account_id).first()
            if data.expense_account_id
            else None
        )
        if expense_account is None:
            raise HTTPException(status_code=404, detail="Expense account not found")
        if paid_from.id == expense_account.id:
            raise HTTPException(
                status_code=400, detail="Expense and paid-from accounts must differ"
            )
        net, itc = split_paid_amount(db, amount, data.tax_amount)
        journal_lines = [
            {
                "account_id": expense_account.id,
                "debit": net,
                "credit": Decimal("0"),
                "description": line_desc,
                "cost_code_id": data.cost_code_id,
                "is_billable": data.is_billable,
                **(
                    {"function": data.function}
                    if "function" in data.model_fields_set
                    else {}
                ),
            }
        ]
        header_job = resolve_job(db, data.job_id, data.customer_id)
    if itc:
        journal_lines.append(
            {
                "account_id": get_itc_account_id(db),
                "debit": itc,
                "credit": Decimal("0"),
                "description": f"HST input tax credit - {line_desc}".rstrip(" -"),
            }
        )
    journal_lines.append(paid_line)
    return journal_lines, header_job, vendor, payee


@router.post("", response_model=ExpenseResponse, status_code=201)
def create_expense(data: ExpenseCreate, db: Session = Depends(get_db)):
    check_closing_date(db, data.date)
    journal_lines, header_job, vendor, payee = _build(db, data)
    txn = create_journal_entry(
        db,
        data.date,
        f"Expense: {payee}" if payee else "Expense",
        journal_lines,
        source_type=SOURCE_TYPE,
        # The vendor is the only linked record an expense has; keep the
        # id where every other posting keeps its origin.
        source_id=vendor.id if vendor else None,
        reference=data.reference or "",
        class_id=data.class_id,
        job_id=header_job,
    )
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db)


@router.put("/{expense_id}", response_model=ExpenseResponse)
def update_expense(expense_id: int, data: ExpenseCreate, db: Session = Depends(get_db)):
    """bsq: open a cheque and change it, as in QuickBooks. The journal is
    posted again in place (same entry, new lines). A cheque that is void,
    reconciled or in a closed period can't be changed; a line already
    billed on an invoice keeps its account, job and amount."""
    from app.routes.invoices.helpers import _reverse_and_delete_journal
    from app.services.billable_costs import billed_snapshot, restore_billed

    txn = (
        db.query(Transaction)
        .filter(Transaction.id == expense_id, Transaction.source_type == SOURCE_TYPE)
        .first()
    )
    if txn is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    if txn.id in _void_ids(db, [txn.id]):
        raise HTTPException(status_code=400, detail="A void cheque can't be changed. Enter it again instead.")
    assert_not_reconciled(txn)
    check_closing_date(db, txn.date)
    check_closing_date(db, data.date)

    journal_lines, header_job, vendor, payee = _build(db, data)
    billed_before = billed_snapshot(db, txn.id)
    # a match to a bank statement line stays when the money out is the same
    from app.models.banking import BankTransaction

    old_paid = next((ln for ln in txn.lines if ln.credit > 0 and ln.account and ln.account.bank_kind), None)
    matched = (
        db.query(BankTransaction).filter(BankTransaction.transaction_line_id == old_paid.id).all()
        if old_paid is not None else []
    )
    keep_match = (
        old_paid is not None
        and old_paid.account_id == data.paid_from_account_id
        and Decimal(str(old_paid.credit)) == Decimal(str(data.amount))
    )
    was_cleared = bool(old_paid is not None and old_paid.cleared)
    if keep_match:
        for bt in matched:
            bt.transaction_line_id = None
        db.flush()
    else:
        release_statement_links(db, txn)
    _reverse_and_delete_journal(db, txn.id)
    db.flush()
    txn.date = data.date
    txn.class_id = data.class_id
    txn.job_id = header_job
    create_journal_entry(
        db,
        data.date,
        f"Expense: {payee}" if payee else "Expense",
        journal_lines,
        source_type=SOURCE_TYPE,
        source_id=vendor.id if vendor else None,
        reference=data.reference or "",
        class_id=data.class_id,
        job_id=header_job,
        existing_transaction=txn,
    )
    restore_billed(db, txn.id, billed_before)
    if keep_match and (matched or was_cleared):
        db.flush()
        db.refresh(txn)
        new_paid = next(
            (ln for ln in txn.lines if ln.credit > 0 and ln.account_id == data.paid_from_account_id),
            None,
        )
        if new_paid is not None:
            new_paid.cleared = was_cleared
            for bt in matched:
                bt.transaction_id = txn.id
                bt.transaction_line_id = new_paid.id
    db.commit()
    db.refresh(txn)
    return _serialize(txn, db)
