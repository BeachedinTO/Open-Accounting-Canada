# bsq: a customer's billable costs not yet on an invoice ("Choose Billable
# Time and Costs" in QuickBooks)

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.billable_costs import unbilled_costs, unbilled_time

router = APIRouter(prefix="/api/billable-costs", tags=["billable-costs"])


@router.get("")
def list_billable_costs(
    customer_id: int = Query(...),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The customer's (or one job's) billable costs not yet invoiced."""
    return unbilled_costs(db, customer_id, job_id)


@router.get("/time")
def list_billable_time(
    customer_id: int = Query(...),
    job_id: Optional[int] = Query(default=None),
    db: Session = Depends(get_db),
):
    """The customer's (or one job's) billable timesheet hours not yet invoiced."""
    return unbilled_time(db, customer_id, job_id)
