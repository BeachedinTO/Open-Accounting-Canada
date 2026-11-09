# ============================================================================
# bsq: open invoices carried over from QuickBooks Desktop's A/R Aging Detail
# report, after the history came across as journal entries (the
# Transaction Detail by Account import). The sales are in that history
# already, so each open invoice comes in as a document for its open
# balance on one line whose item posts to a clearing account (1100 debited,
# the clearing account credited), and each payment QuickBooks left
# unapplied as an unapplied payment (the reverse). One journal then moves
# the carried total from 1100 to the clearing account, which ends at zero:
# Accounts Receivable keeps the balance the history gave it, and the A/R
# aging and every customer's balance now read from documents.
#
# The report as QuickBooks Desktop (Canada) saves it: a header row with
# Type, Date, Num, Name (Customer:Job), Due Date and Open Balance, a
# section per aging bucket, a TOTAL row. Dates 2025-03-31 or 03/31/2025.
# ============================================================================

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.accounts import Account, AccountType
from app.models.invoices import Invoice, InvoiceLine, InvoiceStatus
from app.models.items import Item
from app.models.payments import Payment

CARRY_ITEM = "QuickBooks open balance"
CLEARING = "QuickBooks open balances carried over"
_DATES = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y")


def _money(s: str) -> Decimal:
    s = (s or "").strip().replace(",", "").replace("$", "")
    if not s:
        return Decimal("0")
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = Decimal(s.strip("()"))
    except InvalidOperation:
        raise ValueError(f"not an amount: {s!r}")
    return -v if neg else v


def _day(s: str) -> date:
    for f in _DATES:
        try:
            return datetime.strptime(s.strip(), f).date()
        except ValueError:
            pass
    raise ValueError(f"not a date: {s!r}")


def parse(csv_text: str) -> dict:
    """{items, errors, notes, total}: items are {kind, type, date, num,
    name, due_date, open}; kind "invoice" (open balance > 0) or "payment"
    (a payment or credit memo not applied, open balance < 0)."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    out = {"items": [], "errors": [], "notes": [], "total": None}
    hdr_at = None
    for i, row in enumerate(rows[:40]):
        cells = [c.strip().lower() for c in row]
        if "open balance" in cells and "date" in cells and "type" in cells:
            hdr_at = i
            break
    if hdr_at is None:
        out["errors"].append(
            "This isn't the A/R Aging Detail report: no header row with Type, "
            "Date and Open Balance. In QuickBooks: Reports > Customers & "
            "Receivables > A/R Aging Detail, then Export > CSV."
        )
        return out
    hdr = [c.strip().lower() for c in rows[hdr_at]]
    col = {n: (hdr.index(n) if n in hdr else None) for n in ("type", "date", "num", "name", "due date", "open balance", "amount")}
    if col["name"] is None:
        out["errors"].append("The report has no Name column (the Customer:Job).")
        return out
    cell = lambda r, k: (r[col[k]] if col[k] is not None and col[k] < len(r) else "").strip()
    left = Decimal("0")
    for n, row in enumerate(rows[hdr_at + 1:], start=hdr_at + 2):
        if row and row[0].strip().upper() == "TOTAL":
            try:
                out["total"] = _money(cell(row, "open balance"))
            except ValueError:
                pass
            continue
        ttype, raw = cell(row, "type"), cell(row, "date")
        if not ttype or not raw:
            continue  # an aging bucket's heading or total, a blank
        try:
            day = _day(raw)
            due = _day(cell(row, "due date")) if cell(row, "due date") else day
            open_ = _money(cell(row, "open balance"))
        except ValueError as exc:
            out["errors"].append(f"Row {n}: {exc}")
            continue
        name, num = cell(row, "name"), cell(row, "num")
        what = f"{ttype} {num}".strip() + f" of {day:%Y-%m-%d} for {name or '(no name)'}"
        if not name:
            out["errors"].append(f"Row {n}: {what} names no customer")
            continue
        if open_ > 0:
            kind = "invoice"  # an invoice, or a journal or cheque QuickBooks put in A/R
        elif open_ < 0:
            kind = "payment"  # a payment, deposit, credit memo... not applied
        else:
            continue
        out["items"].append({"kind": kind, "type": ttype, "date": day, "num": num[:50],
                             "name": name[:200], "due_date": due, "open": open_, "row": n})
    carried = sum((i["open"] for i in out["items"]), Decimal("0"))
    if out["total"] is not None and not out["errors"] and carried + left != out["total"]:
        out["errors"].append(
            f"The report's TOTAL ({out['total']:,.2f}) isn't the sum of its rows "
            f"({carried + left:,.2f}): export it again."
        )
    return out


def _clearing(db: Session) -> Account:
    acct = db.query(Account).filter(Account.name == CLEARING).first()
    if acct is None:
        acct = Account(name=CLEARING, account_type=AccountType.ASSET,
                       description="Clearing for open invoices carried over from QuickBooks; ends at zero.")
        db.add(acct)
        db.flush()
    return acct


def _item(db: Session, clearing: Account):
    from app.models.items import Item, ItemType

    item = db.query(Item).filter(Item.name == CARRY_ITEM).first()
    if item is None:
        item = Item(name=CARRY_ITEM, item_type=ItemType.SERVICE, rate=Decimal("0"),
                    is_taxable=False, is_active=True,
                    description="Open balances carried over from QuickBooks: the sales are in the imported history.")
        db.add(item)
    item.income_account_id = clearing.id
    db.flush()
    return item


def _number(db: Session, spec: dict, used: set) -> str:
    """An invoice's own number; anything else (a journal, a cheque) named by
    its type, so it can't take a real invoice's number."""
    if spec["type"].lower() == "invoice" and spec["num"]:
        base = spec["num"]
    else:
        base = f"QB {spec['type']} {spec['num'] or spec['date'].strftime('%Y-%m-%d')}".strip()
    num, k = base[:50], 1
    while num in used or db.query(Invoice).filter(Invoice.invoice_number == num).first():
        k += 1
        num = f"{base[:45]}-{k}"
    used.add(num)
    return num


def carry_over(db: Session, csv_text: str, as_of: date, dry_run: bool = False,
               merge_accounts: tuple = ()) -> dict:
    """Carry the report's open invoices and unapplied payments over.
    Refused whole (nothing written) on any error, when an invoice of that
    customer and number is here already, or when the report's open total
    isn't what Accounts Receivable holds on `as_of`."""
    from sqlalchemy import func

    from app.models.transactions import Transaction, TransactionLine
    from app.routes.invoices.helpers import _post_invoice_journal
    from app.services.accounting import create_journal_entry, get_ar_account_id
    from app.services.jobs_service import resolve_customer_and_job

    p = parse(csv_text)
    result = {"invoices": 0, "payments": 0, "total": 0.0, "errors": list(p["errors"]),
              "notes": p["notes"], "ar_balance": None}
    items = p["items"]
    total = sum((i["open"] for i in items), Decimal("0"))
    result["total"] = float(total)
    ar_id = get_ar_account_id(db)

    def balance(account_id):
        v = db.query(func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0)).join(
            Transaction, Transaction.id == TransactionLine.transaction_id).filter(
            TransactionLine.account_id == account_id, Transaction.date <= as_of).scalar()
        return Decimal(str(v or 0)).quantize(Decimal("0.01"))

    # QuickBooks' other receivable accounts the aging spans (CRA Credit...),
    # merged into Accounts Receivable
    merged = []
    for name in merge_accounts:
        acct = db.query(Account).filter(Account.name == name).first()
        if acct is None:
            result["errors"].append(f"There's no account named {name} to merge into Accounts Receivable.")
        else:
            merged.append((acct, balance(acct.id)))
    ar = balance(ar_id) + sum((b for _, b in merged), Decimal("0"))
    result["ar_balance"] = float(ar)
    if ar != total:
        result["errors"].append(
            f"The open invoices and payments come to {total:,.2f}, but Accounts "
            f"Receivable holds {ar:,.2f} on {as_of:%Y-%m-%d}. Export the aging "
            "report as of the last day of the imported history."
        )
    if db.query(Item).filter(Item.name == CARRY_ITEM).first() is not None and db.query(InvoiceLine).join(
            Item, Item.id == InvoiceLine.item_id).filter(Item.name == CARRY_ITEM).first():
        result["errors"].append("Open invoices were carried over from QuickBooks already; nothing was carried twice.")
    if result["errors"] or dry_run:
        result["invoices"] = sum(1 for i in items if i["kind"] == "invoice")
        result["payments"] = sum(1 for i in items if i["kind"] == "payment")
        return result

    clearing = _clearing(db)
    item = _item(db, clearing)
    # nothing is offset before the books begin (the opening balances)
    first = db.query(func.min(Transaction.date)).scalar() or as_of
    used: set = set()
    for acct, bal in merged:
        if bal:
            dr, cr = (bal, Decimal("0")) if bal > 0 else (Decimal("0"), -bal)
            create_journal_entry(db, as_of, f"{acct.name} merged into Accounts Receivable (as QuickBooks' A/R aging has it)", [
                {"account_id": ar_id, "debit": dr, "credit": cr, "description": f"From {acct.name}"},
                {"account_id": acct.id, "debit": cr, "credit": dr, "description": "Into Accounts Receivable"},
            ], source_type="qb_open_ar", reference=f"QB A/R merge {acct.name}"[:100])

    def _offset(day, amount, what):
        # the history holds this balance in Accounts Receivable already: move
        # it onto the document, on the document's own date, so no balance
        # sheet counts it twice
        dr, cr = (amount, Decimal("0")) if amount > 0 else (Decimal("0"), -amount)
        day = max(day, first)
        create_journal_entry(db, day, f"{what} carried over from QuickBooks: open balance onto the document", [
            {"account_id": clearing.id, "debit": dr, "credit": cr, "description": "Carried over from QuickBooks"},
            {"account_id": ar_id, "debit": cr, "credit": dr, "description": "Carried over from QuickBooks"},
        ], source_type="qb_open_ar", reference=f"QB A/R {what}"[:100])
    for spec in items:
        customer, job = resolve_customer_and_job(db, spec["name"])
        if spec["kind"] == "invoice":
            amount = spec["open"]
            inv = Invoice(
                invoice_number=_number(db, spec, used),
                customer_id=customer.id, job_id=job.id if job else None,
                status=InvoiceStatus.SENT, date=spec["date"], due_date=spec["due_date"],
                terms="", bill_address1=customer.bill_address1, bill_address2=customer.bill_address2,
                subtotal=amount, tax_rate=Decimal("0"), tax_amount=Decimal("0"), total=amount,
                amount_paid=Decimal("0"), balance_due=amount,
                notes="Carried over from QuickBooks' A/R Aging Detail: the sale is in the imported history; this is what was open.",
            )
            db.add(inv)
            db.flush()
            line = InvoiceLine(invoice_id=inv.id, item_id=item.id, job_id=job.id if job else None,
                               description=f"QuickBooks {spec['type'].lower()} {spec['num']} of {spec['date']:%Y-%m-%d}: open balance".replace("  ", " "),
                               quantity=Decimal("1"), rate=amount, amount=amount, is_taxable=False, line_order=0)
            db.add(line)
            db.flush()
            txn = _post_invoice_journal(db, inv, [line], customer.name)
            inv.transaction_id = txn.id
            _offset(spec["date"], amount, f"QuickBooks {spec['type'].lower()} {inv.invoice_number}")
            result["invoices"] += 1
        else:
            amount = -spec["open"]
            pay = Payment(customer_id=customer.id, date=spec["date"], amount=amount,
                          reference=spec["num"] or None, deposit_to_account_id=clearing.id,
                          notes=f"Carried over from QuickBooks: a {spec['type'].lower()} not applied there. The money is in the imported history.")
            db.add(pay)
            db.flush()
            txn = create_journal_entry(db, pay.date, f"{spec['type']} from {customer.name} (carried over from QuickBooks)", [
                {"account_id": clearing.id, "debit": amount, "credit": Decimal("0"), "description": "Unapplied, carried over"},
                {"account_id": ar_id, "debit": Decimal("0"), "credit": amount, "description": "Unapplied, carried over"},
            ], source_type="payment", source_id=pay.id, reference=pay.reference or "")
            pay.transaction_id = txn.id
            _offset(spec["date"], -amount, f"{spec['type']} {spec['num']}".strip())
            result["payments"] += 1
    db.flush()
    return result
