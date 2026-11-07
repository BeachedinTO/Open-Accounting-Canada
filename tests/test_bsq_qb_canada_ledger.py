"""bsq: QuickBooks Desktop Canada's Transaction Detail by Account: dates
2025-01-31, a Trans # column, and Debit/Credit or one Amount column."""

from decimal import Decimal

from app.models.accounts import Account
from app.models.transactions import Transaction
from app.services.qb_report_import import detect_report_type, import_qb_report


def _chart(db):
    for name, t in (("Canada Trust", "asset"), ("Subcontracted Services", "expense"), ("GST/HST Payable", "liability")):
        db.add(Account(name=name, account_type=t))
    db.commit()


TRANS = '''"","Trans #","Type","Date","Num","Name","Memo","Clr","Split","Debit","Credit","Amount","Balance"
"Canada Trust","","","","","","","","","","","",""
"","14001","Cheque","2024-04-03","1656","Heritage Stone","","","-SPLIT-","","1130.00","-1130.00","-1130.00"
"Total Canada Trust","","","","","","","","","0.00","1130.00","-1130.00","-1130.00"
"GST/HST Payable","","","","","","","","","","","",""
"","14001","Cheque","2024-04-03","To Print","Receiver General - payable","HST (ON) on purchases","","Canada Trust","130.00","","130.00","130.00"
"Total GST/HST Payable","","","","","","","","","130.00","0.00","130.00","130.00"
"Subcontracted Services","","","","","","","","","","","",""
"","14001","Cheque","2024-04-03","1656","16 Carey Road","patio","","Canada Trust","1000.00","","1000.00","1000.00"
"Total Subcontracted Services","","","","","","","","","1000.00","0.00","1000.00","1000.00"
"TOTAL","","","","","","","","","1130.00","1130.00","0.00","0.00"
'''


def test_trans_numbers_join_a_cheque_whose_lines_disagree(db_session):
    _chart(db_session)
    assert detect_report_type(TRANS, db_session) == "ledger"
    r = import_qb_report(db_session, TRANS)
    assert r["errors"] == [] and r["journals"] == 1, r
    txn = db_session.query(Transaction).one()
    assert str(txn.date) == "2024-04-03" and "QB Trans # 14001" in txn.reference
    assert sum(l.debit for l in txn.lines) == Decimal("1130.00")
    # a second upload posts nothing twice
    assert import_qb_report(db_session, TRANS)["duplicates_skipped"] == 1


AMOUNT = '''"","Type","Date","Num","Name","Memo","Clr","Split","Amount","Balance"
"Canada Trust","","","","","","","","",""
"","Cheque","2024-04-03","1656","Heritage Stone","","","Subcontracted Services","-1000.00","-1000.00"
"Total Canada Trust","","","","","","","","-1000.00","-1000.00"
"Subcontracted Services","","","","","","","","",""
"","Cheque","2024-04-03","1656","Heritage Stone","","","Canada Trust","1000.00","1000.00"
"Total Subcontracted Services","","","","","","","","1000.00","1000.00"
"TOTAL","","","","","","","","0.00","0.00"
'''


def test_one_amount_column_reads_as_debit_and_credit(db_session):
    _chart(db_session)
    assert detect_report_type(AMOUNT, db_session) == "ledger"
    r = import_qb_report(db_session, AMOUNT)
    assert r["errors"] == [] and r["journals"] == 1, r
    lines = db_session.query(Transaction).one().lines
    assert sorted((l.debit, l.credit) for l in lines) == [(Decimal("0"), Decimal("1000.00")), (Decimal("1000.00"), Decimal("0"))]


def test_a_trans_that_does_not_balance_is_named_and_nothing_posts(db_session):
    _chart(db_session)
    bad = TRANS.replace('"Receiver General - payable","HST (ON) on purchases","","Canada Trust","130.00"', '"Receiver General - payable","HST (ON) on purchases","","Canada Trust","131.00"').replace('"Total GST/HST Payable","","","","","","","","","130.00"', '"Total GST/HST Payable","","","","","","","","","131.00"')
    r = import_qb_report(db_session, bad)
    assert r["journals"] == 0 and any("Trans # 14001" in e for e in r["errors"]), r
    assert db_session.query(Transaction).count() == 0
