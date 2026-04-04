"""
POS Router — Phase 2
Endpoints:
  POST   /pos/transactions           — create a new sale transaction
  GET    /pos/transactions/{id}      — get transaction + receipt
  DELETE /pos/transactions/{id}/void — void (Hard Lock: fails if > 2 min old)
  GET    /pos/reports/daily          — daily sales summary
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services import pos_service

router = APIRouter()


# ─────────────────────────────────────────────
# REQUEST / RESPONSE SCHEMAS
# ─────────────────────────────────────────────

class TransactionItemIn(BaseModel):
    drug_id:        int
    quantity:       int          = Field(..., gt=0)
    unit_price_ngn: float        = Field(..., gt=0)


class CreateTransactionIn(BaseModel):
    pharmacist:     str
    items:          List[TransactionItemIn]
    patient_id:     Optional[int] = None
    payment_method: str           = "cash"
    notes:          Optional[str] = None


class TransactionItemOut(BaseModel):
    drug_id:        int
    drug_name:      Optional[str]
    brand_name:     Optional[str]
    batch_no:       Optional[str]
    quantity:       int
    unit_price_ngn: float
    total_ngn:      float

    class Config:
        from_attributes = True


class ReceiptOut(BaseModel):
    transaction_id: int
    status:         str
    pharmacist:     str
    patient_id:     Optional[int]
    payment_method: str
    items:          List[TransactionItemOut]
    total_ngn:      float
    fx_rate:        float
    notes:          Optional[str]
    created_at:     str
    void_deadline:  str


class DailySalesOut(BaseModel):
    date:              str
    transaction_count: int
    total_revenue_ngn: float
    total_items_sold:  int
    drugs:             List[dict]


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@router.post("/transactions", status_code=201)
def create_transaction(
    payload: CreateTransactionIn,
    db: Session = Depends(get_db),
):
    """
    Create a new POS sale transaction.

    - Validates stock availability for every line item.
    - Deducts stock FIFO (oldest expiry first).
    - Records a StockTransaction audit row per batch touched.
    - Returns the full receipt.
    """
    items = [item.model_dump() for item in payload.items]
    txn = pos_service.create_sale_transaction(
        db=db,
        pharmacist=payload.pharmacist,
        items=items,
        patient_id=payload.patient_id,
        payment_method=payload.payment_method,
        notes=payload.notes,
    )
    return pos_service.assemble_receipt(txn)


@router.get("/transactions/{transaction_id}", response_model=ReceiptOut)
def get_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
):
    """
    Retrieve a transaction and its full receipt by ID.
    """
    txn = pos_service.get_transaction(db, transaction_id)
    return pos_service.assemble_receipt(txn)


@router.delete("/transactions/{transaction_id}/void")
def void_transaction(
    transaction_id: int,
    db: Session = Depends(get_db),
):
    """
    Void a transaction.

    Hard Lock: returns HTTP 409 if the transaction is older than 2 minutes.
    On success, stock is restored and a reversal audit row is written.
    """
    return pos_service.void_transaction(db, transaction_id)


@router.get("/reports/daily", response_model=DailySalesOut)
def daily_report(
    date: str = Query(..., description="Report date in YYYY-MM-DD format"),
    db: Session = Depends(get_db),
):
    """
    Daily sales summary.

    Returns transaction count, total revenue, total items sold,
    and a per-drug breakdown for the given date.
    Voided transactions are excluded.
    """
    return pos_service.daily_sales_report(db, date)
