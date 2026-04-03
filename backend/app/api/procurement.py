"""procurement.py"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, timedelta, datetime
from app.core.database import get_db
from app.models.models import PurchaseOrder, ProcurementLine, Wholesaler, POStatus
from app.services.fx_service import get_cached_fx_rate

router = APIRouter()

class POLineIn(BaseModel):
    drug_id: int
    quantity_ordered: int
    unit_cost_usd: Optional[float] = None

class POCreate(BaseModel):
    wholesaler_id: int
    lines: List[POLineIn]
    notes: Optional[str] = None

@router.get("/")
def list_purchase_orders(db: Session = Depends(get_db)):
    orders = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).limit(50).all()
    return [
        {
            "id": o.id,
            "wholesaler": o.wholesaler.name if o.wholesaler else "Unknown",
            "status": o.status,
            "total_usd": float(o.total_usd or 0),
            "total_ngn": float(o.total_ngn or 0),
            "auto_generated": o.auto_generated,
            "created_at": o.created_at,
            "expected_delivery": o.expected_delivery,
            "line_count": len(o.lines),
        }
        for o in orders
    ]

@router.post("/", status_code=201)
def create_purchase_order(po_in: POCreate, db: Session = Depends(get_db)):
    wholesaler = db.query(Wholesaler).filter(Wholesaler.id == po_in.wholesaler_id).first()
    if not wholesaler:
        raise HTTPException(404, "Wholesaler not found")
    fx = get_cached_fx_rate()
    po = PurchaseOrder(
        wholesaler_id=po_in.wholesaler_id,
        status=POStatus.draft,
        fx_rate=fx,
        expected_delivery=date.today() + timedelta(days=wholesaler.lead_time_days),
        notes=po_in.notes,
    )
    db.add(po)
    db.flush()
    total_usd = 0
    for line in po_in.lines:
        from app.models.models import Drug
        drug = db.query(Drug).filter(Drug.id == line.drug_id).first()
        cost = (line.unit_cost_usd or float(drug.cost_usd)) * line.quantity_ordered
        total_usd += cost
        db.add(ProcurementLine(
            po_id=po.id, drug_id=line.drug_id,
            quantity_ordered=line.quantity_ordered,
            unit_cost_usd=line.unit_cost_usd or drug.cost_usd,
            total_usd=cost,
        ))
    po.total_usd = total_usd
    po.total_ngn = total_usd * fx
    db.commit()
    return {"po_id": po.id, "status": "draft", "total_usd": total_usd, "total_ngn": total_usd * fx}

@router.patch("/{po_id}/approve")
def approve_po(po_id: int, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, "PO not found")
    if po.status != POStatus.draft:
        raise HTTPException(400, f"Cannot approve PO in status: {po.status}")
    po.status = POStatus.approved
    po.approved_at = datetime.utcnow()
    db.commit()
    return {"po_id": po_id, "status": "approved", "message": "PO approved and sent to wholesaler"}

@router.get("/wholesalers")
def list_wholesalers(db: Session = Depends(get_db)):
    return db.query(Wholesaler).filter(Wholesaler.is_active == True).all()
