"""
API Routes — Pharmacy Intelligence Mainframe
All endpoints in one file for clarity (split by module in production)
"""

# ─── dispensing.py ─────────────────────────────────────────────────────────────
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import List, Optional
from decimal import Decimal
from datetime import datetime
import uuid

from app.core.database import get_db
from app.services.clinical_service import clinical_gateway
from app.services.fx_service import PricingEngine, get_cached_fx_rate
from app.models.models import (
    Drug, StockBatch, StockTransaction, DispensingRecord,
    BasketItem, TransactionType, Patient
)

# ─── Schemas ───────────────────────────────────────────────────────────────────

class BasketItemRequest(BaseModel):
    drug_id: int
    quantity: int = Field(ge=1, le=1000)

class DispenseRequest(BaseModel):
    items: List[BasketItemRequest]
    patient_id: Optional[int] = None
    payment_method: str = "cash"
    served_by: str = "Staff"

class DispenseResponse(BaseModel):
    dispensing_id: int
    total_ngn: float
    audit_result: str
    audit_notes: str
    safe_to_dispense: bool
    receipt_ref: str
    items_processed: int
    fx_rate: float

# ─── Router ────────────────────────────────────────────────────────────────────

router = APIRouter()

@router.post("/", response_model=DispenseResponse)
def dispense_basket(request: DispenseRequest, db: Session = Depends(get_db)):
    """
    Core dispensing endpoint.
    1. Audit basket for clinical interactions
    2. Calculate prices at live FX rate
    3. Deduct stock with full transaction trail
    4. Create dispensing record (receipt)
    """
    fx_rate = get_cached_fx_rate()
    engine = PricingEngine(fx_rate=fx_rate)

    # Build basket for clinical audit
    basket_drugs = []
    drug_objects = {}
    batch_objects = {}

    for item in request.items:
        drug = db.query(Drug).filter(Drug.id == item.drug_id, Drug.is_active == True).first()
        if not drug:
            raise HTTPException(status_code=404, detail=f"Drug ID {item.drug_id} not found")

        batch = (
            db.query(StockBatch)
            .filter(
                StockBatch.drug_id == drug.id,
                StockBatch.quantity >= item.quantity,
            )
            .order_by(StockBatch.expiry_date.asc())  # FEFO: First Expired, First Out
            .first()
        )
        if not batch:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for {drug.brand_name}. Available: {sum(b.quantity for b in drug.stock_batches)} units."
            )

        basket_drugs.append({
            "drug_id": drug.id,
            "name": f"{drug.brand_name} {drug.strength}",
            "tags": drug.tags or [],
        })
        drug_objects[item.drug_id] = drug
        batch_objects[item.drug_id] = batch

    # Clinical audit
    audit = clinical_gateway.audit_basket(basket_drugs)

    if not audit.safe_to_dispense:
        # Record the blocked attempt but don't complete the sale
        return DispenseResponse(
            dispensing_id=-1,
            total_ngn=0,
            audit_result=audit.result.value,
            audit_notes=audit.audit_notes,
            safe_to_dispense=False,
            receipt_ref="BLOCKED",
            items_processed=0,
            fx_rate=fx_rate,
        )

    # Build dispensing record
    total_ngn = Decimal("0")
    record = DispensingRecord(
        patient_id=request.patient_id,
        served_by=request.served_by,
        fx_rate=Decimal(str(fx_rate)),
        clinical_audit_passed=True,
        audit_notes=audit.audit_notes,
        payment_method=request.payment_method,
        receipt_qr_code=f"MFRAME-{uuid.uuid4().hex[:12].upper()}",
        total_ngn=Decimal("0"),
    )
    db.add(record)
    db.flush()

    # Process each item
    for item in request.items:
        drug = drug_objects[item.drug_id]
        batch = batch_objects[item.drug_id]
        unit_price = Decimal(str(engine.retail_price_ngn(float(drug.cost_usd))))
        line_total = unit_price * item.quantity
        total_ngn += line_total

        # Deduct stock
        batch.quantity -= item.quantity

        # Transaction log
        tx = StockTransaction(
            batch_id=batch.id,
            transaction_type=TransactionType.sale,
            quantity_change=-item.quantity,
            balance_after=batch.quantity,
            retail_price_ngn=unit_price,
            fx_rate_used=Decimal(str(fx_rate)),
            dispensing_id=record.id,
        )
        db.add(tx)

        # Basket line item
        margin_pct = engine.margin * 100
        bi = BasketItem(
            dispensing_id=record.id,
            drug_id=drug.id,
            batch_id=batch.id,
            quantity=item.quantity,
            unit_price_ngn=unit_price,
            total_ngn=line_total,
            margin_pct=Decimal(str(margin_pct)),
        )
        db.add(bi)

    record.total_ngn = total_ngn
    db.commit()
    db.refresh(record)

    return DispenseResponse(
        dispensing_id=record.id,
        total_ngn=float(total_ngn),
        audit_result=audit.result.value,
        audit_notes=audit.audit_notes,
        safe_to_dispense=True,
        receipt_ref=record.receipt_qr_code,
        items_processed=len(request.items),
        fx_rate=fx_rate,
    )


@router.post("/audit")
def audit_basket_only(request: DispenseRequest, db: Session = Depends(get_db)):
    """Pre-flight audit without completing the sale."""
    basket_drugs = []
    for item in request.items:
        drug = db.query(Drug).filter(Drug.id == item.drug_id).first()
        if drug:
            basket_drugs.append({
                "drug_id": drug.id,
                "name": f"{drug.brand_name} {drug.strength}",
                "tags": drug.tags or [],
            })
    audit = clinical_gateway.audit_basket(basket_drugs)
    return {
        "result": audit.result.value,
        "safe_to_dispense": audit.safe_to_dispense,
        "alerts": [
            {
                "level": a.level.value,
                "drug_a": a.drug_a,
                "drug_b": a.drug_b,
                "message": a.message,
                "action": a.action,
                "reference": a.reference,
            }
            for a in audit.alerts
        ],
        "notes": audit.audit_notes,
    }
