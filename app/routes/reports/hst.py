# ============================================================================
# bsq (Canada): GST/HST return worksheet and remittance.
#
# The worksheet fills the lines of CRA's GST/HST return (regular method)
# from the ledger for a reporting period:
#   101  total sales and other revenue (income accounts, net, before HST)
#   103  GST/HST collected or collectible  (credits less debits on 2200)
#   106  input tax credits                 (debits less credits on 1350)
#   109  net tax = 103 + 104 - (106 + 107); 104/107 adjustments entered
#        by hand on the return if there are any (shown here as 0).
# A remittance entry for an earlier period is left out of the sums, so the
# lines report the period's activity, not what was paid.
#
# Recording the return posts one journal entry: DR 2200 by line 103,
# CR 1350 by line 106, and the bank for the difference (out for an amount
# owing, in for a refund). One entry per period: a second is refused.
# The figures are an aid for filing, not advice; check them before filing.
# ============================================================================

from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.accounts import Account, AccountType
from app.models.transactions import Transaction, TransactionLine
from app.routes.reports._router import router
from app.schemas.common import StrictModel
from app.services import control_accounts
from app.services.accounting import _q, create_journal_entry
from app.services.purchase_posting import ITC_ACCOUNT_NUMBER

SOURCE_TYPE = "hst_return"
VOID_SOURCE_TYPE = "hst_return_void"


def _net(db: Session, account_id: Optional[int], start, end, credit_normal: bool) -> Decimal:
    if account_id is None:
        return _q(0)
    dr, cr = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .filter(
            TransactionLine.account_id == account_id,
            Transaction.date >= start,
            Transaction.date <= end,
            # a recorded return, or its void, is not activity of any period
            (Transaction.source_type.is_(None))
            | (Transaction.source_type.notin_((SOURCE_TYPE, VOID_SOURCE_TYPE))),
        )
        .one()
    )
    dr, cr = Decimal(str(dr)), Decimal(str(cr))
    return _q(cr - dr if credit_normal else dr - cr)


def _revenue(db: Session, start, end) -> Decimal:
    dr, cr = (
        db.query(
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.debit), 0),
            sqlfunc.coalesce(sqlfunc.sum(TransactionLine.credit), 0),
        )
        .join(Transaction, TransactionLine.transaction_id == Transaction.id)
        .join(Account, TransactionLine.account_id == Account.id)
        .filter(
            Account.account_type == AccountType.INCOME,
            Transaction.date >= start,
            Transaction.date <= end,
        )
        .one()
    )
    return _q(Decimal(str(cr)) - Decimal(str(dr)))


def _period_reference(start, end) -> str:
    return f"HST {start.isoformat()} to {end.isoformat()}"


def _filed(db: Session, start, end):
    return (
        db.query(Transaction)
        .filter(
            Transaction.source_type == SOURCE_TYPE,
            Transaction.reference == _period_reference(start, end),
        )
        .first()
    )


def hst_return_lines(db: Session, start, end) -> dict:
    if end < start:
        raise HTTPException(status_code=400, detail="The period ends before it starts.")
    payable_id = control_accounts.find(db, "2200")
    itc_id = control_accounts.find(db, ITC_ACCOUNT_NUMBER)
    l101 = _revenue(db, start, end)
    l103 = _net(db, payable_id, start, end, credit_normal=True)
    l104 = _q(0)
    l105 = l103 + l104
    l106 = _net(db, itc_id, start, end, credit_normal=False)
    l107 = _q(0)
    l108 = l106 + l107
    l109 = l105 - l108
    filed = _filed(db, start, end)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "line_101": float(l101),
        "line_101_rounded": int(l101.quantize(Decimal("1"))),
        "line_103": float(l103),
        "line_104": float(l104),
        "line_105": float(l105),
        "line_106": float(l106),
        "line_107": float(l107),
        "line_108": float(l108),
        "line_109": float(l109),
        "owing": float(l109) if l109 > 0 else 0.0,
        "refund": float(-l109) if l109 < 0 else 0.0,
        "filed_transaction_id": filed.id if filed else None,
        "filed_date": filed.date.isoformat() if filed else None,
    }


@router.get("/hst-return")
def hst_return(
    start_date: dt_date = Query(...),
    end_date: dt_date = Query(...),
    db: Session = Depends(get_db),
):
    return hst_return_lines(db, start_date, end_date)


class HSTFileRequest(StrictModel):
    start_date: dt_date
    end_date: dt_date
    date: dt_date  # when the payment left (or the refund arrived)
    bank_account_id: int
    memo: Optional[str] = None


@router.post("/hst-return/record")
def record_hst_return(data: HSTFileRequest, db: Session = Depends(get_db)):
    """Close the period's HST: DR 2200 (line 103), CR 1350 (line 106), and
    the bank for line 109."""
    from app.services.closing_date import check_closing_date

    check_closing_date(db, data.date)
    if _filed(db, data.start_date, data.end_date):
        raise HTTPException(
            status_code=400,
            detail="This period's HST return is already recorded. Void that entry first to record it again.",
        )
    bank = db.get(Account, data.bank_account_id)
    if bank is None or not bank.bank_kind:
        raise HTTPException(
            status_code=400, detail="Choose the bank account the HST was paid from or refunded to."
        )
    lines = hst_return_lines(db, data.start_date, data.end_date)
    l103, l106, l109 = (Decimal(str(lines[k])) for k in ("line_103", "line_106", "line_109"))
    if l103 == 0 and l106 == 0:
        raise HTTPException(status_code=400, detail="There is no HST in this period to record.")
    ref = _period_reference(data.start_date, data.end_date)
    desc = f"HST return {data.start_date.isoformat()} to {data.end_date.isoformat()}"
    journal = []
    if l103:
        journal.append({"account_id": control_accounts.resolve(db, "2200"),
                        "debit": l103 if l103 > 0 else Decimal("0"),
                        "credit": -l103 if l103 < 0 else Decimal("0"),
                        "description": "HST collected (line 103)"})
    if l106:
        journal.append({"account_id": control_accounts.resolve(db, ITC_ACCOUNT_NUMBER),
                        "debit": -l106 if l106 < 0 else Decimal("0"),
                        "credit": l106 if l106 > 0 else Decimal("0"),
                        "description": "Input tax credits (line 106)"})
    if l109:
        journal.append({"account_id": bank.id,
                        "debit": -l109 if l109 < 0 else Decimal("0"),
                        "credit": l109 if l109 > 0 else Decimal("0"),
                        "description": data.memo or ("HST paid to CRA" if l109 > 0 else "HST refund from CRA")})
    txn = create_journal_entry(db, data.date, desc, journal, source_type=SOURCE_TYPE, reference=ref)
    db.commit()
    return {"status": "ok", "transaction_id": txn.id, **lines,
            "filed_transaction_id": txn.id, "filed_date": data.date.isoformat()}


@router.post("/hst-return/{transaction_id}/void")
def void_hst_return(transaction_id: int, db: Session = Depends(get_db)):
    from app.services.bank_posting import void_document

    txn = db.get(Transaction, transaction_id)
    if txn is None or txn.source_type != SOURCE_TYPE:
        raise HTTPException(status_code=404, detail="HST return entry not found")
    void_document(db, txn, "hst_return_void")
    # free the period so it can be recorded again
    txn.reference = (txn.reference or "") + " (void)"
    db.commit()
    return {"status": "void", "transaction_id": txn.id}
