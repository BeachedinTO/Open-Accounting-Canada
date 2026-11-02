# ============================================================================
# Purchase postings — where a line on a bill, a PO turning into a bill, or a
# vendor credit posts, and what the sales tax on it does. One place, so the
# three documents can't drift apart.
#
# Accounts. A line posts to the account chosen on it, else its item's expense
# account, else the vendor's default expense account. Nothing further: the old
# last resort was account number "6000", which the seeded chart names
# Advertising & Marketing, so a sign shop's panels and a bakery's flour were
# booked as advertising and the P&L showed no cost of goods (2.17.3
# exploratory: skytech W-H5, macbase1 F8). A line nothing names is refused
# with a sentence that says what to choose.
#
# Tax. Sales tax a supplier charges is part of what the purchase cost — a US
# business can't reclaim it — so it is spread over the lines it was charged
# on, in proportion to their amounts, and posts with them (to the expense,
# cost-of-goods or inventory account each line already uses). It never
# touches 2200 Sales Tax Payable, which holds the tax the business collected
# from its own customers and owes the state: debiting it with tax paid to a
# supplier made Pay Sales Tax offer $0.33 where $59.57 was owed (macbase1 F9).
#
# bsq (Canada): with the setting `purchase_tax_recoverable` on, tax on a
# purchase is GST/HST the business claims back as an input tax credit. It is
# then NOT part of the cost: the lines post at their pre-tax amounts and the
# whole tax posts to 1350 HST Recoverable (ITCs), an asset, debited on a bill
# and credited on a vendor credit. It still never touches 2200. Off (the
# default), everything above is unchanged.
# ============================================================================

from decimal import Decimal
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.accounts import Account
from app.services.accounting import _q, quantize_to

COST_Q = Decimal("0.0001")  # unit costs carry four places (InventoryMovement)


def spread(total, amounts: Sequence) -> list[Decimal]:
    """Split `total` over `amounts` in proportion, exact to the cent.

    Each share is first cut down to whole cents, then the cents still left
    go one each to the shares that lost the most in the cut (ties to the
    larger amount, then the earlier line). The shares always add up to
    `total`, and none is negative — which "round each share and give the
    remainder to one line" can't promise: $0.06 over ten $1 lines and a
    1-cent line rounds every $1 share up to a cent and leaves -$0.04 for
    the line that absorbs the difference.

    Amounts of zero or less take no share. Returns one Decimal per amount.
    """
    weights = [Decimal(str(a or 0)) for a in amounts]
    total = _q(total)
    denom = sum((w for w in weights if w > 0), Decimal("0"))
    if total == 0 or denom <= 0:
        return [Decimal("0.00") for _ in weights]
    sign = 1 if total > 0 else -1
    cents = int(abs(total) * 100)
    exact = [(cents * w / denom) if w > 0 else Decimal("0") for w in weights]
    shares = [int(x) for x in exact]  # whole cents, rounded down
    leftover = cents - sum(shares)
    order = sorted(
        (i for i, w in enumerate(weights) if w > 0),
        key=lambda i: (-(exact[i] - shares[i]), -weights[i], i),
    )
    for i in order[:leftover]:
        shares[i] += 1
    return [_q(Decimal(sign * s) / 100) for s in shares]


def unit_cost_with_tax(amount, tax_share, quantity, rate) -> Decimal:
    """What one unit of a stocked line cost once its share of the tax is in.

    The ledger debits Inventory with the line amount plus its tax share, so
    the inventory movement has to carry the same cost or the stock valuation
    (quantity x average cost) stops agreeing with account 1300."""
    qty = Decimal(str(quantity or 0))
    share = Decimal(str(tax_share or 0))
    if share == 0 or qty <= 0:
        return Decimal(str(rate))
    return quantize_to((Decimal(str(amount)) + share) / qty, COST_Q)


ITC_ACCOUNT_NUMBER = "1350"
ITC_ACCOUNT_NAME = "HST Recoverable (ITCs)"


def purchase_tax_recoverable(db: Session) -> bool:
    """True when tax on purchases is claimed back (GST/HST input tax
    credits) rather than being part of what the purchase cost."""
    from app.models.settings import DEFAULT_SETTINGS
    from app.services.settings_service import get_setting_raw

    # an unsaved setting means the default, as the Settings screen shows it
    value = get_setting_raw(db, "purchase_tax_recoverable")
    if value is None:
        value = DEFAULT_SETTINGS.get("purchase_tax_recoverable", "false")
    return (value or "").lower() == "true"


def get_itc_account_id(db: Session) -> int:
    """1350 HST Recoverable (ITCs), created the first time it is needed."""
    from app.models.accounts import AccountType

    acct = (
        db.query(Account)
        .filter(Account.account_number == ITC_ACCOUNT_NUMBER)
        .first()
    )
    if acct is None:
        acct = Account(
            name=ITC_ACCOUNT_NAME,
            account_number=ITC_ACCOUNT_NUMBER,
            account_type=AccountType.ASSET,
            is_system=False,
            balance=Decimal("0"),
        )
        db.add(acct)
        db.flush()
    return acct.id


def purchase_tax_split(db: Session, tax_amount, amounts: Sequence):
    """(per-line tax shares, recoverable tax) for a purchase.

    Tax not recoverable: shares spread over the lines (they carry it as
    cost), recoverable tax 0. Recoverable: every share 0, and the whole tax
    is returned to post to the ITC account."""
    tax = _q(Decimal(str(tax_amount or 0)))
    if tax != 0 and purchase_tax_recoverable(db):
        return [Decimal("0.00") for _ in amounts], tax
    return spread(tax, amounts), Decimal("0.00")


def split_paid_amount(db: Session, amount, tax_amount):
    """(expense part, HST part) of a purchase paid directly (an expense
    paid by cheque or e-transfer, a credit-card charge) whose total
    `amount` includes `tax_amount` of recoverable HST.

    The HST part posts to 1350; the rest to the expense account. A tax
    amount is refused unless Tax on Purchases is set to recoverable, so
    nothing lands on 1350 that the HST return isn't meant to claim."""
    amount = _q(Decimal(str(amount)))
    tax = _q(Decimal(str(tax_amount or 0)))
    if tax == 0:
        return amount, Decimal("0.00")
    if tax < 0:
        raise HTTPException(status_code=400, detail="The HST amount can't be negative.")
    if tax >= amount:
        raise HTTPException(
            status_code=400,
            detail="The HST amount must be less than the total paid (the total includes the HST).",
        )
    if not purchase_tax_recoverable(db):
        raise HTTPException(
            status_code=400,
            detail=(
                "HST on purchases is recorded only when Settings > Tax on "
                "Purchases is set to 'Recoverable: HST input tax credits'."
            ),
        )
    return amount - tax, tax


def itc_amount_of(txn) -> Decimal:
    """The HST input tax credit on one posted transaction (debits less
    credits on 1350)."""
    total = Decimal("0")
    for ln in txn.lines:
        if ln.account is not None and ln.account.account_number == ITC_ACCOUNT_NUMBER:
            total += Decimal(str(ln.debit or 0)) - Decimal(str(ln.credit or 0))
    return _q(total)


def is_itc_line(ln) -> bool:
    return ln.account is not None and ln.account.account_number == ITC_ACCOUNT_NUMBER


def job_for_customer(db: Session, customer_id: int) -> int:
    """bsq: a cost charged to a customer (not one of its jobs) goes to the
    customer's own job, named after the customer, made the first time it is
    needed. Job costing and billable costs follow jobs, so this keeps them
    working when a purchase names only the customer."""
    from sqlalchemy import func

    from app.models.contacts import Customer
    from app.models.jobs import Job

    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    job = (
        db.query(Job)
        .filter(Job.customer_id == customer.id, func.lower(Job.name) == customer.name.lower())
        .order_by(Job.id)
        .first()
    )
    if job is None:
        job = Job(
            customer_id=customer.id,
            name=customer.name,
            status="in_progress",
            is_active=True,
            notes="Made when a cost was first charged to this customer.",
        )
        db.add(job)
        db.flush()
    return job.id


def is_customers_own_job(db: Session, job_id, customer_id) -> bool:
    """True when the job is the one named after its customer."""
    from app.models.contacts import Customer
    from app.models.jobs import Job

    if not job_id or not customer_id:
        return False
    job = db.get(Job, job_id)
    customer = db.get(Customer, customer_id)
    return bool(
        job and customer and job.customer_id == customer.id
        and (job.name or "").lower() == (customer.name or "").lower()
    )


def invoice_customer_job_on(db: Session) -> bool:
    """bsq: the invoice_customer_job setting (see app/models/settings.py)."""
    from app.models.settings import DEFAULT_SETTINGS
    from app.services.settings_service import get_setting_raw

    value = get_setting_raw(db, "invoice_customer_job")
    if value is None:
        value = DEFAULT_SETTINGS.get("invoice_customer_job", "false")
    return (value or "").lower() == "true"


def own_job_if_no_others(db: Session, customer_id: int):
    """bsq: the customer's own job for an invoice that names none, when the
    customer is a single project (no jobs of its own besides the one named
    after it). A customer with real jobs is left alone: the invoice stays
    off any job, as before, rather than landing on a made-up one."""
    from app.models.contacts import Customer
    from app.models.jobs import Job

    customer = db.get(Customer, customer_id)
    if customer is None:
        return None
    others = (
        db.query(Job)
        .filter(Job.customer_id == customer.id)
        .all()
    )
    if any((j.name or "").lower() != (customer.name or "").lower() for j in others):
        return None
    return job_for_customer(db, customer.id)


def resolve_job(db: Session, job_id: Optional[int], customer_id: Optional[int]) -> Optional[int]:
    """The job a purchase line is charged to: the job named, else the
    customer's own job when only a customer is named, else none."""
    if job_id:
        return job_id
    if customer_id:
        return job_for_customer(db, customer_id)
    return None


def split_purchase_lines(db: Session, total, tax_amount, lines, default_desc: str = "", paid_from_id: Optional[int] = None):
    """bsq: a purchase paid directly (cheque, debit, card, cash) split over
    several lines, each with its own expense account, customer or job,
    description and pre-HST amount, plus one HST amount for the receipt.

    Returns (debit journal lines, recoverable HST). The lines plus the HST
    must add up to the total paid, to the cent."""
    total = _q(Decimal(str(total)))
    tax = _q(Decimal(str(tax_amount or 0)))
    if tax < 0:
        raise HTTPException(status_code=400, detail="The HST amount can't be negative.")
    if tax != 0 and not purchase_tax_recoverable(db):
        raise HTTPException(
            status_code=400,
            detail=(
                "HST on purchases is recorded only when Settings > Tax on "
                "Purchases is set to 'Recoverable: HST input tax credits'."
            ),
        )
    out = []
    lines_total = Decimal("0.00")
    for n, ln in enumerate(lines, 1):
        amt = _q(Decimal(str(ln.amount or 0)))
        if amt == 0:
            continue
        acct = db.get(Account, ln.account_id) if ln.account_id else None
        if acct is None:
            raise HTTPException(status_code=400, detail=f"Line {n}: choose the type of expense (the account).")
        if acct.bank_kind or (paid_from_id and acct.id == paid_from_id):
            raise HTTPException(
                status_code=400,
                detail=f"Line {n}: {acct.name} is a bank or card account. A transfer is entered under Banking.",
            )
        out.append(
            {
                "account_id": acct.id,
                "debit": amt if amt > 0 else Decimal("0"),
                "credit": -amt if amt < 0 else Decimal("0"),
                "description": (ln.memo or "").strip() or default_desc or None,
                "job_id": resolve_job(db, ln.job_id, ln.customer_id),
                "is_billable": bool(ln.is_billable),
                "class_id": ln.class_id,
                "cost_code_id": ln.cost_code_id,
            }
        )
        lines_total += amt
    if not out:
        raise HTTPException(status_code=400, detail="Add at least one line: what it was for and how much.")
    if lines_total + tax != total:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The lines (${lines_total:,.2f}) plus HST (${tax:,.2f}) come to "
                f"${lines_total + tax:,.2f}, but the amount paid is ${total:,.2f}. They need to match."
            ),
        )
    return out, tax


def split_lines_out(db: Session, txn) -> list[dict]:
    """The split lines of a posted purchase, for a response: every debit or
    credit except the bank/card side and the HST."""
    from app.models.jobs import Job

    rows = []
    for ln in txn.lines:
        acct = ln.account
        if acct is None or acct.bank_kind or acct.account_number == ITC_ACCOUNT_NUMBER:
            continue
        amount = Decimal(str(ln.debit or 0)) - Decimal(str(ln.credit or 0))
        job = db.get(Job, ln.job_id) if ln.job_id else None
        cust = job.customer.name if job is not None and job.customer is not None else ""
        rows.append(
            {
                "account_id": acct.id,
                "account_name": acct.name,
                "amount": float(amount),
                "job_id": ln.job_id,
                "customer_job": (cust if job is None or job.name == cust else f"{cust} : {job.name}"),
                "memo": ln.description or "",
                "is_billable": bool(getattr(ln, "is_billable", False)),
                # bsq: already on an invoice
                "billed": getattr(ln, "billed_invoice_line_id", None) is not None,
            }
        )
    return rows


def _line_label(line_no: int, description: Optional[str]) -> str:
    desc = (description or "").strip()
    if len(desc) > 40:
        desc = desc[:39].rstrip() + "…"
    return f"Line {line_no} ({desc})" if desc else f"Line {line_no}"


def expense_account_for(
    db: Session,
    *,
    line_no: int,
    description: Optional[str],
    account_id: Optional[int],
    item,
    vendor,
    fix_hint: str = "Choose an account on the line",
) -> int:
    """The account a non-stock purchase line posts to.

    The account chosen on the line, else the item's expense account, else
    the vendor's default expense account. When none of them names one the
    line is refused (400) — never booked to a guessed account."""
    if account_id:
        if db.get(Account, account_id) is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{_line_label(line_no, description)} names an account that "
                    "isn't in the chart of accounts. Choose another account. "
                    "Nothing was saved."
                ),
            )
        return account_id
    if item is not None and item.expense_account_id:
        return item.expense_account_id
    if vendor is not None and vendor.default_expense_account_id:
        return vendor.default_expense_account_id
    who = f"{vendor.name} " if vendor is not None and vendor.name else "the vendor "
    raise HTTPException(
        status_code=400,
        detail=(
            f"{_line_label(line_no, description)} has no account to post to. "
            f"{fix_hint}, or give {who}a default expense account in the "
            "Vendor Center. Nothing was saved."
        ),
    )
