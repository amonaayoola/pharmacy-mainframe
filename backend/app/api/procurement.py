from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, timedelta, datetime

from app.core.database import get_db
from app.core.auth import get_current_user
from app.models.models import (
    PurchaseOrder, ProcurementLine, Wholesaler, POStatus, Drug,
    ProcurementBudget,
)

router = APIRouter(prefix="/api/procurement", tags=["procurement"])

class BudgetUpsert(BaseModel):
    category: str
    amount_usd: float
    month: int
    year: int

class ApproveRequest(BaseModel):
    approved_by: str
    budget_override: bool = False
    override_reason: Optional[str] = None

@router.get("/budget")
def monthly_budget_summary(
    year:  Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    from app.services.procurement_service import get_budget_summary
    return get_budget_summary(db, year, month)

@router.post("/budget", status_code=201)
def upsert_budget(body: BudgetUpsert, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    from app.services.procurement_service import upsert_budget_amount
    return upsert_budget_amount(db, body.category, body.amount_usd, body.month, body.year)

@router.post("/orders/{po_id}/approve")
def approve_purchase_order(
    po_id: int,
    body: ApproveRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user),
):
    from app.services.procurement_service import approve_po
    try:
        return approve_po(
            db,
            po_id,
            approved_by=body.approved_by,
            budget_override=body.budget_override,
            override_reason=body.override_reason,
            current_user=current_user,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
