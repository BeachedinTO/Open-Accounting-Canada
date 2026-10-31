from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field
from app.schemas.common import StrictModel


class PurchaseLineCreate(StrictModel):
    """bsq: one line of a split purchase: the type of expense (account), the
    customer or job it is for, a description, and its amount before HST."""

    account_id: Optional[int] = None
    amount: Decimal = Decimal("0")
    customer_id: Optional[int] = None
    job_id: Optional[int] = None
    memo: Optional[str] = None
    is_billable: bool = False
    class_id: Optional[int] = None
    cost_code_id: Optional[int] = None


class ExpenseCreate(StrictModel):
    """One paid receipt: money already left a bank or credit-card account.
    DR expense account, CR the account it was paid from."""

    date: dt_date
    vendor_id: Optional[int] = None
    payee: Optional[str] = Field(None, max_length=200)
    expense_account_id: Optional[int] = None  # bsq: or `lines`
    paid_from_account_id: int
    amount: Decimal
    # bsq: HST included in `amount`, claimed as an input tax credit (1350)
    tax_amount: Decimal = Decimal("0")
    reference: Optional[str] = Field(None, max_length=100)
    memo: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    customer_id: Optional[int] = None  # bsq: charges the customer's own job
    cost_code_id: Optional[int] = None
    function: Optional[str] = None  # nonprofit: program | management | fundraising
    is_billable: bool = False
    # bsq: split over several lines (each pre-HST); amount = lines + HST
    lines: list[PurchaseLineCreate] = []


class ExpenseResponse(BaseModel):
    id: int
    date: dt_date
    payee: str = ""
    vendor_id: Optional[int] = None
    expense_account_name: str = ""
    paid_from_account_name: str = ""
    amount: float
    tax_amount: float = 0.0  # bsq: HST input tax credit included in amount
    reference: str = ""
    memo: str = ""
    status: str = "recorded"  # "recorded" | "void"
    lines: list[dict] = []  # bsq: the split lines
