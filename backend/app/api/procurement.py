"""
procurement.py — Phase 3: Full Auto-Procurement Intelligence
Covers: PO lifecycle, rules engine, budget management, compliance, audit trail.
Backward-compatible with Phase 1C endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime, timedelta

from app.core.database import get_db
from app.models.models import (
    PurchaseOrder, ProcurementLine, Wholesaler, POStatus, Drug,
    ProcurementBudget, Vendor,
)
from app.models.procurement_models import (
    ProcurementRule, BudgetLimit, ApprovalThreshold, POApproval, POTracking
)
from app.services.fx_service import get_cached_fx_rate
from app.services.inventory_service import list_auto_generated_pos
from app.services.procurement_service import (
    auto_generate_pos,
    approve_po,
    dispatch_po,
    receive_po,
    get_budget_summary,
    submit_po_for_approval,
    cancel_po,
    get_active_po_tracking,
    simulate_order,
    apply_procurement_rules,
    calculate_order_quantity,
    select_vendor,
    _generate_po_number,
    _log_tracking_event,
)
from app.services.budget_service import (
    get_budget_status,
    monthly_reconciliation,
)
from app.services.compliance_service import (
    get_compliance_check,
    add_compliance_flag,
    get_audit_trail,
)

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class POLineIn(BaseModel):
    drug_id: int
    quantity_ordered: int
    unit_cost_usd: Optional[float] = None
    vendor_id: Optional[int] = None

class POCreate(BaseModel):
    vendor_id: Optional[int] = None
    wholesaler_id: Optional[int] = None
    lines: List[POLineIn]
    notes: Optional[str] = None
    created_by: Optional[str] = "staff"

class ApproveRequest(BaseModel):
    approved_by: str = "pharmacist"
    budget_override: bool = False
    override_reason: Optional[str] = None

class SubmitRequest(BaseModel):
    submitted_by: str = "staff"

class CancelRequest(BaseModel):
    reason: str
    cancelled_by: str = "staff"

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

# Phase 3B schemas
class RuleCreate(BaseModel):
    name: str
    rule_type: str   # stock_based / vendor_based / budget_based
    condition: dict
    action: dict
    priority: int = 10
    active: bool = True

class RuleUpdate(BaseModel):
    name: Optional[str] = None
    condition: Optional[dict] = None
    action: Optional[dict] = None
    priority: Optional[int] = None
    active: Optional[bool] = None

class BudgetLimitCreate(BaseModel):
    category: str
    vendor_id: Optional[int] = None
    monthly_limit: float
    reset_date: Optional[date] = None

class ApprovalThresholdCreate(BaseModel):
    threshold_amount: float
    required_approver_role: str
    escalate_to_owner: bool = False

class ComplianceFlagCreate(BaseModel):
    flag_type: str
    reason: str
    severity: str = "warning"
    vendor_id: Optional[int] = None
    drug_id: Optional[int] = None
    expires_at: Optional[datetime] = None

class SeasonalForecastIn(BaseModel):
    drug_id: int
    month: int
    demand_multiplier: float
    reason: Optional[str] = None


# ── List / Create POs ─────────────────────────────────────────────────────────

@router.get("/po")
def list_purchase_orders(
    status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
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
    return [_po_out(o) for o in q.limit(limit).all()]


@router.post("/po", status_code=201)
def create_purchase_order(po_in: POCreate, db: Session = Depends(get_db)):
    """Create a draft PO (manual or vendor-based)."""
    # Resolve supplier
    wholesaler_id = po_in.wholesaler_id
    vendor_id = po_in.vendor_id
    lead_time = 3

    if vendor_id:
        vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
        if not vendor:
            raise HTTPException(404, "Vendor not found")
        lead_time = vendor.lead_time_days
    elif wholesaler_id:
        wholesaler = db.query(Wholesaler).filter(Wholesaler.id == wholesaler_id).first()
        if not wholesaler:
            raise HTTPException(404, "Wholesaler not found")
        lead_time = wholesaler.lead_time_days
    else:
        raise HTTPException(400, "Either vendor_id or wholesaler_id is required")

    fx = get_cached_fx_rate()
    po = PurchaseOrder(
        wholesaler_id=wholesaler_id,
        vendor_id=vendor_id,
        status=POStatus.draft,
        fx_rate=fx,
        expected_delivery=date.today() + timedelta(days=lead_time),
        notes=po_in.notes,
        created_by=po_in.created_by,
        auto_generated=False,
    )
    db.add(po)
    db.flush()

    # Generate PO number
    po.po_number = _generate_po_number(db)

    total_usd = 0.0
    total_ngn = 0.0
    for line in po_in.lines:
        drug = db.query(Drug).filter(Drug.id == line.drug_id).first()
        if not drug:
            raise HTTPException(404, f"Drug #{line.drug_id} not found")
        cost_usd = line.unit_cost_usd or float(drug.cost_usd)
        cost_ngn = round(cost_usd * fx, 2)
        qty = line.quantity_ordered
        total_usd += cost_usd * qty
        total_ngn += cost_ngn * qty
        db.add(ProcurementLine(
            po_id=po.id,
            drug_id=line.drug_id,
            vendor_id=line.vendor_id or vendor_id,
            quantity_ordered=qty,
            unit_cost_usd=cost_usd,
            unit_cost_ngn=cost_ngn,
            total_usd=round(cost_usd * qty, 4),
            total_ngn=round(cost_ngn * qty, 2),
        ))

    po.total_usd = round(total_usd, 4)
    po.total_ngn = round(total_ngn, 2)
    _log_tracking_event(db, po.id, "created", f"Draft PO created by {po_in.created_by}")
    db.commit()
    return _po_out(po)


@router.get("/po/tracking")
def active_po_tracking(db: Session = Depends(get_db)):
    """List all active POs with their latest tracking event."""
    return get_active_po_tracking(db)


@router.get("/po/{po_id}")
def get_purchase_order(po_id: int, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, "PO not found")
    out = _po_out(po)
    out["lines"] = [_line_out(l) for l in po.lines]
    out["tracking"] = [
        {"event": t.event, "timestamp": t.timestamp.isoformat(), "notes": t.notes}
        for t in sorted(po.tracking_events, key=lambda x: x.timestamp)
    ]
    out["approvals"] = [
        {"approver_id": a.approver_id, "status": a.status, "approved_at": a.approved_at, "notes": a.notes}
        for a in po.approvals
    ]
    return out


@router.patch("/po/{po_id}")
def update_draft_po(po_id: int, notes: Optional[str] = None, db: Session = Depends(get_db)):
    """Update notes on a draft PO."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, "PO not found")
    if po.status != POStatus.draft:
        raise HTTPException(400, "Only draft POs can be updated")
    if notes is not None:
        po.notes = notes
    db.commit()
    return _po_out(po)


# ── PO lifecycle actions ──────────────────────────────────────────────────────

@router.post("/po/{po_id}/submit")
def submit_po(po_id: int, body: SubmitRequest, db: Session = Depends(get_db)):
    """Submit a draft PO for approval."""
    try:
        return submit_po_for_approval(db, po_id, body.submitted_by)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/po/{po_id}/approve")
def approve_purchase_order(po_id: int, body: ApproveRequest, db: Session = Depends(get_db)):
    """Approve a PO. Runs budget checks per drug category."""
    try:
        result = approve_po(
            db, po_id,
            approved_by=body.approved_by,
            budget_override=body.budget_override,
            override_reason=body.override_reason,
        )
        _log_tracking_event(db, po_id, "approved", f"Approved by {body.approved_by}")
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/po/{po_id}/cancel")
def cancel_purchase_order(po_id: int, body: CancelRequest, db: Session = Depends(get_db)):
    """Cancel a PO that hasn't been received."""
    try:
        return cancel_po(db, po_id, body.reason, body.cancelled_by)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/po/{po_id}/dispatch")
def dispatch_purchase_order(po_id: int, db: Session = Depends(get_db)):
    """Mark an approved PO as dispatched/ordered."""
    try:
        result = dispatch_po(db, po_id)
        _log_tracking_event(db, po_id, "dispatched")
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/po/{po_id}/receive")
def receive_purchase_order(po_id: int, body: ReceiveRequest, db: Session = Depends(get_db)):
    """Record goods receipt. Creates StockBatch entries and StockTransactions."""
    try:
        result = receive_po(db, po_id, [l.dict() for l in body.lines])
        _log_tracking_event(db, po_id, "received", f"Goods received: {len(body.lines)} lines")
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Phase 3B: Rules engine ────────────────────────────────────────────────────

@router.post("/rules", status_code=201)
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    """Create a procurement rule."""
    rule = ProcurementRule(**body.dict())
    db.add(rule)
    db.commit()
    return _rule_out(rule)


@router.get("/rules")
def list_rules(active_only: bool = False, db: Session = Depends(get_db)):
    q = db.query(ProcurementRule)
    if active_only:
        q = q.filter(ProcurementRule.active == True)
    return [_rule_out(r) for r in q.order_by(ProcurementRule.priority).all()]


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleUpdate, db: Session = Depends(get_db)):
    rule = db.query(ProcurementRule).filter(ProcurementRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    for field, val in body.dict(exclude_unset=True).items():
        setattr(rule, field, val)
    db.commit()
    return _rule_out(rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.query(ProcurementRule).filter(ProcurementRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    db.commit()


# ── Phase 3B: Budget limits ───────────────────────────────────────────────────

@router.post("/budget-limits", status_code=201)
def create_budget_limit(body: BudgetLimitCreate, db: Session = Depends(get_db)):
    """Create a budget limit for a category (and optionally a specific vendor)."""
    limit = BudgetLimit(
        category=body.category,
        vendor_id=body.vendor_id,
        monthly_limit=body.monthly_limit,
        current_spent=0,
        reset_date=body.reset_date or date(date.today().year, date.today().month, 1) + timedelta(days=32),
    )
    db.add(limit)
    db.commit()
    return _budget_limit_out(limit)


@router.get("/budget-limits")
def list_budget_limits(db: Session = Depends(get_db)):
    limits = db.query(BudgetLimit).all()
    return [_budget_limit_out(l) for l in limits]


@router.patch("/budget/{category}")
def update_category_budget(
    category: str,
    monthly_budget_ngn: float,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Update the monthly budget ceiling for a drug category."""
    today = date.today()
    y = year or today.year
    m = month or today.month
    existing = db.query(ProcurementBudget).filter_by(category=category, year=y, month=m).first()
    if existing:
        existing.monthly_budget_ngn = monthly_budget_ngn
        existing.updated_at = datetime.utcnow()
    else:
        existing = ProcurementBudget(
            category=category, year=y, month=m,
            monthly_budget_ngn=monthly_budget_ngn, spent_ngn=0
        )
        db.add(existing)
    db.commit()
    return {"category": category, "year": y, "month": m, "monthly_budget_ngn": monthly_budget_ngn}


# ── Phase 3B: Simulate order ──────────────────────────────────────────────────

@router.get("/simulate-order/{drug_id}")
def simulate_drug_order(drug_id: int, db: Session = Depends(get_db)):
    """Simulate a procurement order: vendor selection, qty, cost, budget check, rules."""
    try:
        return simulate_order(db, drug_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Phase 3D: Budget status ───────────────────────────────────────────────────

@router.get("/budget-status")
def budget_status(db: Session = Depends(get_db)):
    """Current month's full budget status across all categories."""
    return get_budget_status(db)


# ── Phase 3D: Compliance ──────────────────────────────────────────────────────

@router.get("/compliance-check")
def compliance_check(db: Session = Depends(get_db)):
    """Full compliance snapshot: NAFDAC, expiry, active flags."""
    return get_compliance_check(db)


@router.post("/compliance-flags", status_code=201)
def create_compliance_flag(body: ComplianceFlagCreate, db: Session = Depends(get_db)):
    """Create a compliance flag for a vendor or drug."""
    try:
        return add_compliance_flag(
            db,
            flag_type=body.flag_type,
            reason=body.reason,
            severity=body.severity,
            vendor_id=body.vendor_id,
            drug_id=body.drug_id,
            expires_at=body.expires_at,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Phase 3D: Reconciliation ──────────────────────────────────────────────────

@router.post("/reconcile")
def reconcile(
    reconciled_by: str = "pharmacist",
    month: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Run monthly reconciliation of ordered vs received goods."""
    return monthly_reconciliation(db, reconciled_by, month)


# ── Phase 3D: Seasonality ─────────────────────────────────────────────────────

@router.get("/seasonality")
def get_seasonality(drug_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Return seasonal demand multipliers."""
    from app.models.procurement_models import SeasonalForecast
    q = db.query(SeasonalForecast)
    if drug_id:
        q = q.filter(SeasonalForecast.drug_id == drug_id)
    forecasts = q.order_by(SeasonalForecast.drug_id, SeasonalForecast.month).all()
    return [
        {
            "id": f.id,
            "drug_id": f.drug_id,
            "drug_name": f.drug.generic_name if f.drug else None,
            "month": f.month,
            "demand_multiplier": float(f.demand_multiplier),
            "reason": f.reason,
        }
        for f in forecasts
    ]


@router.post("/seasonality", status_code=201)
def upsert_seasonality(body: SeasonalForecastIn, db: Session = Depends(get_db)):
    """Create or update a seasonal demand multiplier."""
    from app.models.procurement_models import SeasonalForecast
    drug = db.query(Drug).filter(Drug.id == body.drug_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")
    if not (1 <= body.month <= 12):
        raise HTTPException(400, "month must be between 1 and 12")
    if body.demand_multiplier <= 0:
        raise HTTPException(400, "demand_multiplier must be positive")

    existing = db.query(SeasonalForecast).filter_by(drug_id=body.drug_id, month=body.month).first()
    if existing:
        existing.demand_multiplier = body.demand_multiplier
        existing.reason = body.reason
    else:
        existing = SeasonalForecast(
            drug_id=body.drug_id, month=body.month,
            demand_multiplier=body.demand_multiplier, reason=body.reason
        )
        db.add(existing)
    db.commit()
    return {"drug_id": body.drug_id, "month": body.month, "demand_multiplier": body.demand_multiplier}


# ── Phase 3D: Audit trail ─────────────────────────────────────────────────────

@router.get("/audit-trail")
def audit_trail(limit: int = 100, db: Session = Depends(get_db)):
    """Return PO event audit trail, most recent first."""
    return get_audit_trail(db, limit)


# ── Auto-generate ─────────────────────────────────────────────────────────────

@router.post("/auto-generate")
def trigger_auto_generate(db: Session = Depends(get_db)):
    """Scan low-stock alerts and auto-create draft POs."""
    return auto_generate_pos(db)


# ── Legacy Phase 1C endpoints (backward compatible) ──────────────────────────

@router.get("/orders")
def list_orders_legacy(
    status: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
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
    return [_po_out(o) for o in q.limit(limit).all()]


@router.post("/orders", status_code=201)
def create_order_legacy(po_in: POCreate, db: Session = Depends(get_db)):
    return create_purchase_order(po_in, db)


@router.post("/orders/{po_id}/approve")
def approve_order_legacy(po_id: int, body: ApproveRequest, db: Session = Depends(get_db)):
    return approve_purchase_order(po_id, body, db)


@router.post("/orders/{po_id}/dispatch")
def dispatch_order_legacy(po_id: int, db: Session = Depends(get_db)):
    return dispatch_purchase_order(po_id, db)


@router.post("/orders/{po_id}/receive")
def receive_order_legacy(po_id: int, body: ReceiveRequest, db: Session = Depends(get_db)):
    return receive_purchase_order(po_id, body, db)


@router.get("/budget")
def monthly_budget_legacy(
    year: Optional[int] = None, month: Optional[int] = None, db: Session = Depends(get_db)
):
    return get_budget_summary(db, year=year, month=month)


@router.post("/budget", status_code=201)
def upsert_budget_legacy(body: BudgetUpsert, db: Session = Depends(get_db)):
    today = date.today()
    y = body.year or today.year
    m = body.month or today.month
    existing = db.query(ProcurementBudget).filter_by(category=body.category, year=y, month=m).first()
    if existing:
        existing.monthly_budget_ngn = body.monthly_budget_ngn
        existing.updated_at = datetime.utcnow()
    else:
        existing = ProcurementBudget(
            category=body.category, year=y, month=m,
            monthly_budget_ngn=body.monthly_budget_ngn, spent_ngn=0,
        )
        db.add(existing)
    db.commit()
    return {"category": existing.category, "year": y, "month": m, "monthly_budget_ngn": float(existing.monthly_budget_ngn)}


@router.get("/")
def list_pos_legacy(db: Session = Depends(get_db)):
    orders = db.query(PurchaseOrder).order_by(PurchaseOrder.created_at.desc()).limit(50).all()
    return [_po_out(o) for o in orders]


@router.patch("/{po_id}/approve")
def approve_po_legacy(po_id: int, db: Session = Depends(get_db)):
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(404, "PO not found")
    if po.status != POStatus.draft:
        raise HTTPException(400, f"Cannot approve PO in status: {po.status}")
    po.status = POStatus.approved
    po.approved_at = datetime.utcnow()
    db.commit()
    return {"po_id": po_id, "status": "approved"}


@router.get("/wholesalers")
def list_wholesalers(db: Session = Depends(get_db)):
    return db.query(Wholesaler).filter(Wholesaler.is_active == True).all()


@router.get("/auto-generated")
def list_auto_generated(limit: int = 50, db: Session = Depends(get_db)):
    pos = list_auto_generated_pos(db, limit=min(limit, 200))
    return {"count": len(pos), "purchase_orders": pos}


# ── Serialisers ───────────────────────────────────────────────────────────────

def _po_out(o: PurchaseOrder) -> dict:
    return {
        "id": o.id,
        "po_number": o.po_number,
        "wholesaler": o.wholesaler.name if o.wholesaler else None,
        "vendor_id": o.vendor_id,
        "vendor_name": o.vendor.name if o.vendor else None,
        "status": o.status,
        "total_usd": float(o.total_usd or 0),
        "total_ngn": float(o.total_ngn or 0),
        "fx_rate": float(o.fx_rate or 0),
        "auto_generated": o.auto_generated,
        "created_by": o.created_by,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "approved_by": o.approved_by,
        "dispatched_at": o.dispatched_at.isoformat() if o.dispatched_at else None,
        "received_at": o.received_at.isoformat() if o.received_at else None,
        "expected_delivery": o.expected_delivery.isoformat() if o.expected_delivery else None,
        "notes": o.notes,
        "line_count": len(o.lines),
    }


def _line_out(l: ProcurementLine) -> dict:
    return {
        "id": l.id,
        "drug_id": l.drug_id,
        "drug_name": l.drug.generic_name if l.drug else None,
        "vendor_id": l.vendor_id,
        "quantity_ordered": l.quantity_ordered,
        "quantity_received": l.quantity_received,
        "unit_cost_usd": float(l.unit_cost_usd) if l.unit_cost_usd else None,
        "unit_cost_ngn": float(l.unit_cost_ngn) if l.unit_cost_ngn else None,
        "total_usd": float(l.total_usd) if l.total_usd else None,
        "total_ngn": float(l.total_ngn) if l.total_ngn else None,
    }


def _rule_out(r: ProcurementRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "rule_type": r.rule_type,
        "condition": r.condition,
        "action": r.action,
        "priority": r.priority,
        "active": r.active,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _budget_limit_out(l: BudgetLimit) -> dict:
    return {
        "id": l.id,
        "category": l.category,
        "vendor_id": l.vendor_id,
        "monthly_limit": float(l.monthly_limit),
        "current_spent": float(l.current_spent),
        "reset_date": l.reset_date.isoformat() if l.reset_date else None,
    }
