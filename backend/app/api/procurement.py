"""
procurement.py — Phase 3: Auto-Procurement Intelligence
Full PO lifecycle + budget controls + auto-generation from inventory alerts.
Extends Phase 1C endpoints (kept backward-compatible).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, timedelta, datetime

from app.core.database import get_db
from app.models.models import (
    PurchaseOrder, ProcurementLine, Wholesaler, POStatus, Drug,
    ProcurementBudget,
)
from app.services.fx_service import get_cached_fx_rate
from app.services.inventory_service import list_auto_generated_pos
from app.services.procurement_service import (
    auto_generate_pos,
    approve_po,
    dispatch_po,
    receive_po,
    get_budget_summary,
)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class POLineIn(BaseModel):
    drug_id: int
    quantity_ordered: int
    unit_cost_usd: Optional[float] = None

class POCreate(BaseModel):
    wholesaler_id: int
    lines: List[POLineIn]
    notes: Optional[str] = None

class ApproveRequest(BaseModel):
    approved_by: str = "pharmacist"
    budget_override: bool = False
    override_reason: Optional[str] = None

class ReceiveLine(BaseModel):
    line_id: int
    quantity_received: int

class ReceiveRequest(BaseModel):
    lines: List[ReceiveLine]

class BudgetUpsert(BaseModel):
    category: str
    monthly_budget_ngn: float
    year: Optional[int] = None
    month: Optional[int] = None


# ── List / Create POs ─────────────────────────────────────────────────────────

@router.get("/orders")
def list_purchase_orders(
    status: Optional[str] = Query(None, description="Filter by PO status"),
    from_date: Optional[date] = Query(None),
    to_date:   Optional[date] = Query(None),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc())
    if status:
        try:
            q = q.filter(PurchaseOrder.status == POStatus(status))
        except ValueError:
            raise HTTPException(400, f"Invalid status '{status}'")
    if from_date:
        q = q.filter(PurchaseOrder.created_at >= from_date)
    if to_date:
        q = q.filter(PurchaseOrder.created_at <= to_date)
    orders = q.limit(limit).all()
    return [_po_out(o) for o in orders]


@router.post("/orders", status_code=201)
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
    total_usd = 0.0
    for line in po_in.lines:
        drug = db.query(Drug).filter(Drug.id == line.drug_id).first()
        if not drug:
            raise HTTPException(404, f"Drug #{line.drug_id} not found")
        cost_usd = line.unit_cost_usd or float(drug.cost_usd)
        cost_ngn = round(cost_usd * fx, 2)
        qty      = line.quantity_ordered
        total_usd += cost_usd * qty
        db.add(ProcurementLine(
            po_id=po.id,
            drug_id=line.drug_id,
            quantity_ordered=qty,
            unit_cost_usd=cost_usd,
            unit_cost_ngn=cost_ngn,
            total_usd=round(cost_usd * qty, 4),
            total_ngn=round(cost_ngn * qty, 2),
        ))
    po.total_usd = round(total_usd, 4)
    po.total_ngn = round(total_usd * fx, 2)
    db.commit()
    return {"po_id": po.id, "status": "draft", "total_usd": po.total_usd, "total_ngn": po.total_ngn}


# ── Phase 3: Auto-generate from inventory alerts ──────────────────────────────

@router.post("/auto-generate")
def trigger_auto_generate(db: Session = Depends(get_db)):
    """
    Scan current low-stock inventory alerts and auto-create draft POs.
    Applies NAFDAC check, smart vendor selection, EOQ calculation, and FX conversion.
    """
    return auto_generate_pos(db)


# ── Phase 3: Approve ──────────────────────────────────────────────────────────

@router.post("/orders/{po_id}/approve")
def approve_purchase_order(
    po_id: int,
    body: ApproveRequest,
    db: Session = Depends(get_db),
):
    """
    Approve a draft PO. Runs budget checks per drug category.
    Pass budget_override=true (admin only) to bypass budget ceiling.
    """
    try:
        return approve_po(
            db,
            po_id,
            approved_by=body.approved_by,
            budget_override=body.budget_override,
            override_reason=body.override_reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Phase 3: Dispatch ─────────────────────────────────────────────────────────

@router.post("/orders/{po_id}/dispatch")
def dispatch_purchase_order(po_id: int, db: Session = Depends(get_db)):
    """Mark an approved PO as ordered and record dispatch timestamp."""
    try:
        return dispatch_po(db, po_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Phase 3: Receive ──────────────────────────────────────────────────────────

@router.post("/orders/{po_id}/receive")
def receive_purchase_order(
    po_id: int,
    body: ReceiveRequest,
    db: Session = Depends(get_db),
):
    """
    Record goods receipt. Creates StockBatch entries and StockTransactions.
    body.lines: [{line_id, quantity_received}]
    """
    try:
        return receive_po(db, po_id, [l.dict() for l in body.lines])
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Phase 3: Budget ───────────────────────────────────────────────────────────

@router.get("/budget")
def monthly_budget_summary(
    year:  Optional[int] = Query(None),
    month: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Monthly procurement budget summary per drug category."""
    return get_budget_summary(db, year=year, month=month)


@router.post("/budget", status_code=201)
def upsert_budget(body: BudgetUpsert, db: Session = Depends(get_db)):
    """Create or update a monthly budget ceiling for a drug category."""
    today = date.today()
    y = body.year  or today.year
    m = body.month or today.month
    existing = (
        db.query(ProcurementBudget)
        .filter_by(category=body.category, year=y, month=m)
        .first()
    )
    if existing:
        existing.monthly_budget_ngn = body.monthly_budget_ngn
        existing.updated_at = datetime.utcnow()
    else:
        existing = ProcurementBudget(
            category=body.category,
            year=y,
            month=m,
            monthly_budget_ngn=body.monthly_budget_ngn,
            spent_ngn=0,
        )
        db.add(existing)
    db.commit()
    return {
        "category":           existing.category,
        "year":               y,
        "month":              m,
        "monthly_budget_ngn": float(existing.monthly_budget_ngn),
    }


# ── Legacy Phase 1C endpoints (kept for backward compat) ─────────────────────

@router.get("/")
def list_pos_legacy(db: Session = Depends(get_db)):
    orders = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).limit(50).all()
    return [_po_out(o) for o in orders]


@router.patch("/{po_id}/approve")
def approve_po_legacy(po_id: int, db: Session = Depends(get_db)):
    """Legacy approve endpoint (no body). Kept for backward compat."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, "PO not found")
    if po.status != POStatus.draft:
        raise HTTPException(400, f"Cannot approve PO in status: {po.status}")
    po.status      = POStatus.approved
    po.approved_at = datetime.utcnow()
    db.commit()
    return {"po_id": po_id, "status": "approved"}


@router.get("/wholesalers")
def list_wholesalers(db: Session = Depends(get_db)):
    return db.query(Wholesaler).filter(Wholesaler.is_active == True).all()


@router.get("/auto-generated")
def list_auto_generated(limit: int = 50, db: Session = Depends(get_db)):
    """Phase 1C — list auto-generated POs."""
    pos = list_auto_generated_pos(db, limit=min(limit, 200))
    return {"count": len(pos), "purchase_orders": pos}


# ── Serialiser ────────────────────────────────────────────────────────────────

def _po_out(o: PurchaseOrder) -> dict:
    return {
        "id":                o.id,
        "wholesaler":        o.wholesaler.name if o.wholesaler else None,
        "vendor_id":         o.vendor_id,
        "status":            o.status,
        "total_usd":         float(o.total_usd or 0),
        "total_ngn":         float(o.total_ngn or 0),
        "fx_rate":           float(o.fx_rate or 0),
        "auto_generated":    o.auto_generated,
        "created_at":        o.created_at,
        "approved_at":       o.approved_at,
        "approved_by":       o.approved_by,
        "dispatched_at":     o.dispatched_at,
        "received_at":       o.received_at,
        "expected_delivery": o.expected_delivery,
        "line_count":        len(o.lines),
    }
