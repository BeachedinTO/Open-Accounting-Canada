"""bsq: open invoices from QuickBooks Desktop's A/R Aging Detail, carried
over onto a history imported as journal entries."""

from datetime import date
from decimal import Decimal

from app.models.invoices import Invoice
from app.models.transactions import Transaction, TransactionLine
from app.services.accounting import create_journal_entry, get_ar_account_id
from app.services.qb_desktop_open_ar import carry_over

AGING = '''"","Type","Date","Num","P. O. #","Name","Terms","Due Date","Class","Aging","Open Balance"
"Current","","","","","","","","","",""
"","Invoice","2025-03-20","1912","","535 Logan Avenue","","2025-04-19","","","5,650.00"
"Total Current","","","","","","","","","","5,650.00"
"31 - 60","","","","","","","","","",""
"","Invoice","2025-02-10","1890","","The Robinson Group - Construction:8 Braeside Crescent","","2025-02-10","","49","2,000.00"
"","Payment","2025-02-12","","","31 Lippincott St","","2025-02-12","","","-500.00"
"Total 31 - 60","","","","","","","","","","1,500.00"
"TOTAL","","","","","","","","","","7,150.00"
'''


def _bal(db, account_id, day):
    from sqlalchemy import func
    return Decimal(str(db.query(func.coalesce(func.sum(TransactionLine.debit - TransactionLine.credit), 0)).join(
        Transaction, Transaction.id == TransactionLine.transaction_id).filter(
        TransactionLine.account_id == account_id, Transaction.date <= day).scalar())).quantize(Decimal("0.01"))


def _history(db, seed_accounts, amount, day):
    """the sales, in the history as journal entries"""
    dr, cr = (amount, Decimal("0")) if amount > 0 else (Decimal("0"), -amount)
    create_journal_entry(db, day, "history", [
        {"account_id": get_ar_account_id(db), "debit": dr, "credit": cr},
        {"account_id": seed_accounts["4000"].id, "debit": cr, "credit": dr},
    ], source_type="qb_ledger_import")
    db.commit()


def test_open_invoices_become_documents_and_ar_is_unchanged(db_session, seed_accounts):
    _history(db_session, seed_accounts, Decimal("2000.00"), date(2025, 2, 10))
    _history(db_session, seed_accounts, Decimal("5650.00"), date(2025, 3, 20))
    _history(db_session, seed_accounts, Decimal("-500.00"), date(2025, 2, 12))
    ar = get_ar_account_id(db_session)
    before = {d: _bal(db_session, ar, d) for d in (date(2025, 2, 11), date(2025, 2, 28), date(2025, 3, 31))}
    r = carry_over(db_session, AGING, date(2025, 3, 31))
    db_session.commit()
    assert r["errors"] == [] and (r["invoices"], r["payments"]) == (2, 1), r
    # A/R and income as the history had them, on every date
    assert {d: _bal(db_session, ar, d) for d in before} == before
    assert _bal(db_session, seed_accounts["4000"].id, date(2025, 3, 31)) == Decimal("-7150.00")
    inv = db_session.query(Invoice).filter(Invoice.invoice_number == "1890").one()
    assert inv.balance_due == Decimal("2000.00") and str(inv.due_date) == "2025-02-10"
    from app.models.jobs import Job
    assert inv.customer.name == "The Robinson Group - Construction"
    assert db_session.get(Job, inv.job_id).name == "8 Braeside Crescent"


def test_a_total_that_differs_from_ar_is_refused(db_session, seed_accounts):
    _history(db_session, seed_accounts, Decimal("1000.00"), date(2025, 3, 1))
    r = carry_over(db_session, AGING, date(2025, 3, 31))
    assert r["invoices"] == 2 and any("Accounts Receivable holds 1,000.00" in e for e in r["errors"])
    assert db_session.query(Invoice).count() == 0


def test_other_receivable_accounts_merge_and_journal_rows_come_too(db_session, seed_accounts):
    """bsq's aging spans Accounts Receivable and CRA Credit, and lists HST
    journals QuickBooks put in A/R; all of it comes over, CRA Credit is
    merged into A/R, and the total on every date is unchanged."""
    from app.models.accounts import Account

    cra = Account(name="CRA Credit", account_type="asset")
    db_session.add(cra)
    db_session.commit()
    ar = get_ar_account_id(db_session)
    _history(db_session, seed_accounts, Decimal("5650.00"), date(2025, 3, 20))
    create_journal_entry(db_session, date(2025, 3, 31), "HST refund", [
        {"account_id": cra.id, "debit": Decimal("100.00"), "credit": Decimal("0")},
        {"account_id": seed_accounts["2000"].id, "debit": Decimal("0"), "credit": Decimal("100.00")},
    ], source_type="qb_ledger_import")
    db_session.commit()
    aging = '''"","Type","Date","Num","P. O. #","Name","Terms","Due Date","Aging","Open Balance"
"","Invoice","2025-03-20","1912","","535 Logan Avenue","","2025-04-19","","5,650.00"
"","General Journal","2025-03-31","GST/HSTCA0062","","Receiver General","","","","100.00"
"TOTAL","","","","","","","","","5,750.00"
'''
    r = carry_over(db_session, aging, date(2025, 3, 31), merge_accounts=("CRA Credit",))
    db_session.commit()
    assert r["errors"] == [] and r["invoices"] == 2, r
    assert _bal(db_session, ar, date(2025, 3, 31)) == Decimal("5750.00")
    assert _bal(db_session, cra.id, date(2025, 3, 31)) == Decimal("0.00")
    assert db_session.query(Invoice).filter(Invoice.invoice_number == "QB General Journal GST/HSTCA0062").one().balance_due == Decimal("100.00")
    # a second run carries nothing twice
    again = carry_over(db_session, aging, date(2025, 3, 31), merge_accounts=("CRA Credit",))
    assert any("already" in e for e in again["errors"])
