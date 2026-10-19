from datetime import date as dt_date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from app.schemas.common import StrictModel


class CCChargeCreate(StrictModel):
    date: dt_date
    payee: Optional[str] = None
    account_id: int
    # the card (a liability account; default 2100 Credit Card)
    card_account_id: Optional[int] = None
    amount: Decimal
    # bsq: HST included in `amount`, claimed as an input tax credit (1350)
    tax_amount: Decimal = Decimal("0")
    memo: Optional[str] = None
    reference: Optional[str] = None
    class_id: Optional[int] = None
    job_id: Optional[int] = None
    function: Optional[str] = None  # nonprofit: program | management | fundraising


class CCChargeResponse(BaseModel):
    id: int
    date: dt_date
    payee: str = ""
    account_name: str = ""
    amount: float
    memo: str = ""
    reference: str = ""
