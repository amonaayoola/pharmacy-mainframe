"""
Procurement Service — Phase 3: Auto-Procurement Intelligence
Vendor selection, PO lifecycle, budget controls, NAFDAC checks, FX integration.
Extended: Phase 3B rules engine, 3C PO lifecycle helpers.
"""

import logging
import random
import string
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (
    Drug,
    POStatus,
    ProcurementBudget,
    ProcurementLine,
    PurchaseOrder,
    StockBatch,
    Vendor,
    VendorDrugPrice,
    NAFDACStatus,
)
from app.models.procurement_models import (
    ProcurementRule,
    BudgetLimit,
    ApprovalThreshold,
    POApproval,
    POTracking,
    SeasonalForecast,
    VendorRelationship,
)
from app.services.fx_service import get_cached_fx_rate
from app.services.inventory_service import get_inventory_alerts, calculate_eoq, get_drug_velocity

logger = logging.getLogger(__name__)


# ── Vendor helpers ────────────────────────────────────────────────────────────

def _composite_score(price_ngn: float, lead_time_days: int, performance_score: float) -> float:
    """
    Lower is better.
    score = (price_ngn / 1000) + lead_time_days - performance_score
    Performance score (0–10) offsets cost/lead-time so high-performers rank better.
    """
    return (price_ngn / 1_000) + lead_time_days - float(performance_score)


def select_best_vendor(db: Session, drug_id: int) -> Optional[Dict]:
    """
    Returns the best vendor for a drug based on composite score
    (price × lead time vs performance).  Returns None if no vendor has pricing.
    """
    prices = (
        db.query(VendorDrugPrice)
        .join(Vendor, VendorDrugPrice.vendor_id == Vendor.id)
        .filter(
            VendorDrugPrice.drug_id == drug_id,
            Vendor.is_active == True,
        )
        .all()
    )
    if not prices:
        return None

    best = min(
        prices,
        key=lambda p: _composite_score(
            float(p.unit_price_ngn),
            p.vendor.lead_time_days,
            p.vendor.performance_score,
        ),
    )
    return {
        "vendor_id":       best.vendor.id,
        "vendor_name":     best.vendor.name,
        "unit_price_ngn":  float(best.unit_price_ngn),
        "unit_price_usd":  float(best.unit_price_usd) if best.unit_price_usd else None,
        "lead_time_days":  best.vendor.lead_time_days,
        "performance_score": float(best.vendor.performance_score),
    }


# ── Budget helpers ────────────────────────────────────────────────────────────

def _get_or_create_budget(db: Session, category: str, year: int, month: int) -> ProcurementBudget:
    budget = (
        db.query(ProcurementBudget)
        .filter_by(category=category, year=year, month=month)
        .first()
    )
    if not budget:
        # Default budget: unlimited (0 = no ceiling enforced)
        budget = ProcurementBudget(
            category=category,
            year=year,
            month=month,
            monthly_budget_ngn=0,
            spent_ngn=0,
        )
        db.add(budget)
        db.flush()
    return budget


def check_budget(
    db: Session,
    category: str,
    amount_ngn: float,
    budget_override: bool = False,
) -> Dict:
    """
    Returns {"allowed": bool, "reason": str, "remaining_ngn": float}.
    If monthly_budget_ngn == 0, the category has no ceiling → always allowed.
    """
    today = date.today()
    budget = _get_or_create_budget(db, category, today.year, today.month)
    ceiling = float(budget.monthly_budget_ngn)
    spent   = float(budget.spent_ngn)
    remaining = ceiling - spent if ceiling > 0 else float("inf")

    if ceiling == 0:
        return {"allowed": True, "reason": "No budget ceiling set", "remaining_ngn": None}

    if spent + amount_ngn > ceiling:
        if budget_override:
            return {
                "allowed":       True,
                "reason":        f"Over budget — admin override accepted. Ceiling ₦{ceiling:,.0f}, spent ₦{spent:,.0f}",
                "remaining_ngn": remaining,
            }
        return {
            "allowed":       False,
            "reason":        f"Monthly budget exceeded. Ceiling ₦{ceiling:,.0f}, spent ₦{spent:,.0f}, requested ₦{amount_ngn:,.0f}",
            "remaining_ngn": remaining,
        }

    return {"allowed": True, "reason": "Within budget", "remaining_ngn": remaining - amount_ngn}


def _deduct_budget(db: Session, category: str, amount_ngn: float) -> None:
    today = date.today()
    budget = _get_or_create_budget(db, category, today.year, today.month)
    budget.spent_ngn = float(budget.spent_ngn) + amount_ngn
    db.flush()


# ── Auto-generate POs from inventory alerts ───────────────────────────────────

def auto_generate_pos(db: Session) -> Dict:
    """
    Scans current low-stock inventory alerts and creates draft POs.

    Logic:
    1. Pull low_stock bucket from inventory alerts.
    2. For each drug, check NAFDAC status — skip if not verified.
    3. Select best vendor (Phase 3) or fall back to best Wholesaler.
    4. Skip if a pending auto-PO already exists for this drug.
    5. Calculate EOQ, convert price to NGN using live FX.
    6. Create PurchaseOrder + ProcurementLine.
    """
    from app.models.models import Wholesaler

    fx_rate = get_cached_fx_rate()
    today   = date.today()
    alerts  = get_inventory_alerts(db)
    low_stock_items = alerts.get("low_stock", [])

    if not low_stock_items:
        return {
            "status":          "ok",
            "generated_at":    datetime.utcnow().isoformat() + "Z",
            "pos_created":     0,
            "skipped":         [],
            "purchase_orders": [],
        }

    created_pos: List[Dict] = []
    skipped:     List[Dict] = []

    for alert in low_stock_items:
        drug_id   = alert["drug_id"]
        drug      = db.query(Drug).filter(Drug.id == drug_id).first()
        if not drug:
            continue

        # ── NAFDAC check ──────────────────────────────────────────────────
        # A drug is orderable if it has a nafdac_reg_no (registered)
        # We treat absence of NAFDAC reg_no as unverified
        if not drug.nafdac_reg_no:
            skipped.append({
                "drug_id":      drug_id,
                "generic_name": drug.generic_name,
                "reason":       "No NAFDAC registration number — cannot order unregistered drug",
            })
            logger.warning(f"Skipping drug_id={drug_id} ({drug.generic_name}): no NAFDAC reg_no")
            continue

        # ── Duplicate-PO guard ─────────────────────────────────────────────
        existing = (
            db.query(PurchaseOrder)
            .join(ProcurementLine, PurchaseOrder.id == ProcurementLine.po_id)
            .filter(
                ProcurementLine.drug_id == drug_id,
                PurchaseOrder.auto_generated == True,
                PurchaseOrder.status.in_([POStatus.draft, POStatus.approved, POStatus.ordered]),
            )
            .first()
        )
        if existing:
            skipped.append({
                "drug_id":      drug_id,
                "generic_name": drug.generic_name,
                "reason":       f"Pending auto-PO already exists (PO #{existing.id}, {existing.status})",
            })
            continue

        # ── Vendor / pricing selection ─────────────────────────────────────
        vendor_info = select_best_vendor(db, drug_id)
        if vendor_info:
            unit_price_ngn = vendor_info["unit_price_ngn"]
            unit_price_usd = vendor_info["unit_price_usd"] or (unit_price_ngn / fx_rate)
            vendor_id      = vendor_info["vendor_id"]
            lead_time      = vendor_info["lead_time_days"]
            wholesaler_id  = None
        else:
            # Fall back to best Wholesaler (legacy)
            wholesaler = (
                db.query(Wholesaler)
                .filter(Wholesaler.is_active == True)
                .order_by(Wholesaler.rating.desc())
                .first()
            )
            if not wholesaler:
                skipped.append({
                    "drug_id":      drug_id,
                    "generic_name": drug.generic_name,
                    "reason":       "No active vendors or wholesalers configured",
                })
                continue
            cost_usd       = float(drug.cost_usd) if drug.cost_usd else 0
            unit_price_usd = cost_usd
            unit_price_ngn = round(cost_usd * fx_rate, 2)
            vendor_id      = None
            lead_time      = wholesaler.lead_time_days or 3
            wholesaler_id  = wholesaler.id

        # ── EOQ ────────────────────────────────────────────────────────────
        velocity = get_drug_velocity(db, drug_id)
        eoq      = calculate_eoq(velocity, unit_price_usd)
        total_usd = round(eoq * unit_price_usd, 4)
        total_ngn = round(eoq * unit_price_ngn, 2)

        # ── Create PO ─────────────────────────────────────────────────────
        po = PurchaseOrder(
            wholesaler_id=wholesaler_id,
            vendor_id=vendor_id,
            status=POStatus.draft,
            fx_rate=fx_rate,
            total_usd=total_usd,
            total_ngn=total_ngn,
            expected_delivery=today + timedelta(days=lead_time),
            auto_generated=True,
            notes=(
                f"Auto-generated by Phase 3 Procurement Engine. "
                f"Drug: {drug.generic_name} ({drug.brand_name}). "
                f"Velocity: {velocity:.2f}/day. "
                f"Days of supply: {alert.get('days_of_supply', '?')}. EOQ: {eoq}."
            ),
        )
        db.add(po)
        db.flush()

        db.add(ProcurementLine(
            po_id=po.id,
            drug_id=drug_id,
            vendor_id=vendor_id,
            quantity_ordered=eoq,
            unit_cost_usd=unit_price_usd,
            unit_cost_ngn=unit_price_ngn,
            total_usd=total_usd,
            total_ngn=total_ngn,
        ))

        created_pos.append({
            "po_id":             po.id,
            "drug_id":           drug_id,
            "generic_name":      drug.generic_name,
            "brand_name":        drug.brand_name,
            "vendor":            vendor_info["vendor_name"] if vendor_info else wholesaler_id,
            "quantity_ordered":  eoq,
            "unit_price_ngn":    unit_price_ngn,
            "unit_price_usd":    unit_price_usd,
            "total_ngn":         total_ngn,
            "total_usd":         total_usd,
            "expected_delivery": (today + timedelta(days=lead_time)).isoformat(),
        })
        logger.info(f"Auto-PO #{po.id} created for {drug.generic_name}, qty={eoq}")

    db.commit()
    return {
        "status":          "ok",
        "generated_at":    datetime.utcnow().isoformat() + "Z",
        "fx_rate_ngn":     fx_rate,
        "pos_created":     len(created_pos),
        "skipped_count":   len(skipped),
        "purchase_orders": created_pos,
        "skipped":         skipped,
    }


# ── PO lifecycle ──────────────────────────────────────────────────────────────

def approve_po(
    db: Session,
    po_id: int,
    approved_by: str,
    budget_override: bool = False,
    override_reason: Optional[str] = None,
) -> Dict:
    """
    Approves a draft PO after budget checks.
    Raises ValueError for business rule violations.
    """
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"PO #{po_id} not found")
    if po.status != POStatus.draft:
        raise ValueError(f"Cannot approve PO in status: {po.status}")

    # ── Budget check per drug category ───────────────────────────────────
    # Aggregate lines by drug_class
    budget_checks: Dict[str, float] = {}
    for line in po.lines:
        drug = db.query(Drug).filter(Drug.id == line.drug_id).first()
        cat  = (drug.drug_class or "Uncategorized") if drug else "Uncategorized"
        line_ngn = float(line.total_ngn or 0)
        budget_checks[cat] = budget_checks.get(cat, 0) + line_ngn

    blocked: List[str] = []
    for cat, amount in budget_checks.items():
        result = check_budget(db, cat, amount, budget_override=budget_override)
        if not result["allowed"]:
            blocked.append(f"{cat}: {result['reason']}")

    if blocked:
        raise ValueError("Budget exceeded — " + "; ".join(blocked))

    # All good — approve
    po.status          = POStatus.approved
    po.approved_at     = datetime.utcnow()
    po.approved_by     = approved_by
    po.budget_override = budget_override
    if override_reason:
        po.override_reason = override_reason

    # Deduct budget
    for cat, amount in budget_checks.items():
        _deduct_budget(db, cat, amount)

    db.commit()
    return {
        "po_id":       po_id,
        "status":      "approved",
        "approved_by": approved_by,
        "approved_at": po.approved_at.isoformat(),
    }


def dispatch_po(db: Session, po_id: int) -> Dict:
    """Marks PO as ordered and records dispatch timestamp."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"PO #{po_id} not found")
    if po.status != POStatus.approved:
        raise ValueError(f"PO must be approved before dispatch. Current status: {po.status}")

    po.status       = POStatus.ordered
    po.dispatched_at = datetime.utcnow()
    # Legacy field kept in sync
    po.sent_at      = po.dispatched_at
    db.commit()
    return {
        "po_id":         po_id,
        "status":        "ordered",
        "dispatched_at": po.dispatched_at.isoformat(),
    }


def receive_po(db: Session, po_id: int, received_lines: List[Dict]) -> Dict:
    """
    Records goods receipt for a PO.
    received_lines: [{"line_id": int, "quantity_received": int}]

    For each line:
    - Updates ProcurementLine.quantity_received
    - Creates a new StockBatch (or tops-up existing batch if batch_no matches)
    - Records a StockTransaction (procurement type)
    """
    from app.models.models import StockTransaction, TransactionType
    import random, string

    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"PO #{po_id} not found")
    if po.status not in (POStatus.ordered, POStatus.approved):
        raise ValueError(f"Cannot receive PO in status: {po.status}")

    fx_rate   = float(po.fx_rate or get_cached_fx_rate())
    today     = date.today()
    received_summary: List[Dict] = []

    line_map = {line.id: line for line in po.lines}

    for item in received_lines:
        line_id  = item["line_id"]
        qty_recv = int(item["quantity_received"])
        line     = line_map.get(line_id)
        if not line:
            raise ValueError(f"ProcurementLine #{line_id} not found on PO #{po_id}")
        if qty_recv < 0:
            raise ValueError(f"Received quantity cannot be negative (line {line_id})")

        line.quantity_received = qty_recv

        # Generate a batch number
        rand_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        batch_no    = f"PO{po_id}-L{line_id}-{rand_suffix}"

        # Create stock batch
        batch = StockBatch(
            drug_id=line.drug_id,
            batch_no=batch_no,
            quantity=qty_recv,
            unit_cost_usd=line.unit_cost_usd,
            expiry_date=today + timedelta(days=365),  # default 1-year expiry; update manually
            received_at=datetime.utcnow(),
        )
        db.add(batch)
        db.flush()

        # Stock transaction
        db.add(StockTransaction(
            batch_id=batch.id,
            transaction_type=TransactionType.procurement,
            quantity_change=qty_recv,
            balance_after=qty_recv,
            fx_rate_used=fx_rate,
            notes=f"Received via PO #{po_id} (Phase 3 auto-procurement)",
        ))

        received_summary.append({
            "line_id":          line_id,
            "drug_id":          line.drug_id,
            "quantity_received": qty_recv,
            "batch_no":         batch_no,
            "batch_id":         batch.id,
        })

    po.status      = POStatus.received
    po.received_at = datetime.utcnow()
    db.commit()

    return {
        "po_id":            po_id,
        "status":           "received",
        "received_at":      po.received_at.isoformat(),
        "lines_received":   received_summary,
    }


# ── Budget summary ────────────────────────────────────────────────────────────

def get_budget_summary(db: Session, year: Optional[int] = None, month: Optional[int] = None) -> Dict:
    today = date.today()
    y = year  or today.year
    m = month or today.month

    budgets = (
        db.query(ProcurementBudget)
        .filter_by(year=y, month=m)
        .all()
    )

    rows = [
        {
            "category":           b.category,
            "monthly_budget_ngn": float(b.monthly_budget_ngn),
            "spent_ngn":          float(b.spent_ngn),
            "remaining_ngn":      float(b.monthly_budget_ngn) - float(b.spent_ngn)
                                  if float(b.monthly_budget_ngn) > 0 else None,
            "utilisation_pct":    round(float(b.spent_ngn) / float(b.monthly_budget_ngn) * 100, 1)
                                  if float(b.monthly_budget_ngn) > 0 else None,
        }
        for b in budgets
    ]

    return {
        "year":   y,
        "month":  m,
        "budgets": rows,
        "total_budget_ngn": sum(r["monthly_budget_ngn"] for r in rows),
        "total_spent_ngn":  sum(r["spent_ngn"] for r in rows),
    }


# ── Phase 3B: Rules Engine ────────────────────────────────────────────────────

def apply_procurement_rules(db: Session, drug_id: int, current_stock: int) -> Dict:
    """
    Evaluate all active procurement rules against a drug's current state.
    Returns a list of triggered rules and recommended actions.
    """
    drug = db.query(Drug).filter(Drug.id == drug_id).first()
    if not drug:
        raise ValueError(f"Drug #{drug_id} not found")

    rules = (
        db.query(ProcurementRule)
        .filter(ProcurementRule.active == True)
        .order_by(ProcurementRule.priority)
        .all()
    )

    triggered = []
    for rule in rules:
        condition = rule.condition or {}
        matched = False

        if rule.rule_type == "stock_based":
            threshold = condition.get("min_stock", 0)
            if current_stock <= threshold:
                matched = True

        elif rule.rule_type == "budget_based":
            category = condition.get("category", drug.drug_class)
            today = date.today()
            budget = (
                db.query(ProcurementBudget)
                .filter_by(category=category, year=today.year, month=today.month)
                .first()
            )
            if budget:
                utilisation = float(budget.spent_ngn) / float(budget.monthly_budget_ngn) * 100 if float(budget.monthly_budget_ngn) > 0 else 0
                threshold_pct = condition.get("utilisation_pct_exceeds", 80)
                if utilisation >= threshold_pct:
                    matched = True

        elif rule.rule_type == "vendor_based":
            min_score = condition.get("min_performance_score", 5.0)
            vendor_info = select_best_vendor(db, drug_id)
            if vendor_info and vendor_info["performance_score"] < min_score:
                matched = True

        if matched:
            triggered.append({
                "rule_id": rule.id,
                "rule_name": rule.name,
                "rule_type": rule.rule_type,
                "action": rule.action,
            })

    return {
        "drug_id": drug_id,
        "drug_name": drug.generic_name,
        "current_stock": current_stock,
        "rules_evaluated": len(rules),
        "triggered": triggered,
    }


def calculate_order_quantity(db: Session, drug_id: int) -> Dict:
    """
    Calculate optimal order quantity using EOQ + lead time buffer + seasonal adjustment.
    """
    drug = db.query(Drug).filter(Drug.id == drug_id).first()
    if not drug:
        raise ValueError(f"Drug #{drug_id} not found")

    velocity = get_drug_velocity(db, drug_id)
    cost_usd = float(drug.cost_usd) if drug.cost_usd else 1.0
    eoq = calculate_eoq(velocity, cost_usd)

    # Lead time buffer
    vendor_info = select_best_vendor(db, drug_id)
    lead_time = vendor_info["lead_time_days"] if vendor_info else 3
    buffer = int(velocity * lead_time)

    # Seasonal adjustment
    today = date.today()
    seasonal = (
        db.query(SeasonalForecast)
        .filter_by(drug_id=drug_id, month=today.month)
        .first()
    )
    multiplier = float(seasonal.demand_multiplier) if seasonal else 1.0

    recommended_qty = max(int((eoq + buffer) * multiplier), 1)

    return {
        "drug_id": drug_id,
        "drug_name": drug.generic_name,
        "daily_velocity": velocity,
        "eoq": eoq,
        "lead_time_buffer": buffer,
        "seasonal_multiplier": multiplier,
        "recommended_order_qty": recommended_qty,
    }


def select_vendor(db: Session, drug_id: int) -> Optional[Dict]:
    """
    Select best vendor with relationship status factored in.
    Suspended vendors are excluded. Primary vendors get a score bonus.
    """
    prices = (
        db.query(VendorDrugPrice)
        .join(Vendor, VendorDrugPrice.vendor_id == Vendor.id)
        .filter(
            VendorDrugPrice.drug_id == drug_id,
            Vendor.is_active == True,
        )
        .all()
    )
    if not prices:
        return None

    candidates = []
    for p in prices:
        rel = db.query(VendorRelationship).filter_by(vendor_id=p.vendor_id).first()
        # Skip suspended vendors
        if rel and rel.status == "suspended":
            continue

        score = _composite_score(
            float(p.unit_price_ngn),
            p.vendor.lead_time_days,
            p.vendor.performance_score,
        )
        # Primary vendor gets a 2-point bonus (lower score = better)
        if rel and rel.status == "primary":
            score -= 2.0

        candidates.append((score, p))

    if not candidates:
        return None

    best_score, best = min(candidates, key=lambda x: x[0])
    rel = db.query(VendorRelationship).filter_by(vendor_id=best.vendor_id).first()

    return {
        "vendor_id": best.vendor.id,
        "vendor_name": best.vendor.name,
        "unit_price_ngn": float(best.unit_price_ngn),
        "unit_price_usd": float(best.unit_price_usd) if best.unit_price_usd else None,
        "lead_time_days": best.vendor.lead_time_days,
        "performance_score": float(best.vendor.performance_score),
        "relationship_status": rel.status if rel else "unknown",
    }


def simulate_order(db: Session, drug_id: int) -> Dict:
    """
    Simulate a procurement order for a drug — runs rules, selects vendor, calculates qty and cost.
    Does NOT create any records.
    """
    drug = db.query(Drug).filter(Drug.id == drug_id).first()
    if not drug:
        raise ValueError(f"Drug #{drug_id} not found")

    # Stock check
    from sqlalchemy import func as sqlfunc
    from app.models.models import StockBatch
    current_stock = db.query(sqlfunc.sum(StockBatch.quantity)).filter(
        StockBatch.drug_id == drug_id
    ).scalar() or 0

    qty_info = calculate_order_quantity(db, drug_id)
    vendor_info = select_vendor(db, drug_id)

    fx_rate = get_cached_fx_rate()

    total_ngn = None
    total_usd = None
    if vendor_info:
        total_ngn = round(vendor_info["unit_price_ngn"] * qty_info["recommended_order_qty"], 2)
        unit_usd = vendor_info["unit_price_usd"] or vendor_info["unit_price_ngn"] / fx_rate
        total_usd = round(unit_usd * qty_info["recommended_order_qty"], 4)

    # Budget check
    category = drug.drug_class or "Uncategorized"
    budget_check = check_budget(db, category, total_ngn or 0)

    # Rules check
    rules_result = apply_procurement_rules(db, drug_id, current_stock)

    return {
        "drug_id": drug_id,
        "drug_name": drug.generic_name,
        "brand_name": drug.brand_name,
        "current_stock": current_stock,
        "recommended_order_qty": qty_info["recommended_order_qty"],
        "selected_vendor": vendor_info,
        "total_ngn": total_ngn,
        "total_usd": total_usd,
        "fx_rate": fx_rate,
        "budget_check": budget_check,
        "rules_triggered": rules_result["triggered"],
        "nafdac_registered": bool(drug.nafdac_reg_no),
    }


# ── Phase 3C: PO Number + Tracking helpers ────────────────────────────────────

def _generate_po_number(db: Session) -> str:
    """Generate a sequential PO number like PO-2026-0042."""
    year = date.today().year
    count = db.query(PurchaseOrder).filter(
        func.extract("year", PurchaseOrder.created_at) == year
    ).count()
    return f"PO-{year}-{count + 1:04d}"


def _log_tracking_event(db: Session, po_id: int, event: str, notes: Optional[str] = None) -> None:
    """Append a tracking event to the PO's event log."""
    db.add(POTracking(po_id=po_id, event=event, notes=notes))
    db.flush()


def submit_po_for_approval(db: Session, po_id: int, submitted_by: str) -> Dict:
    """Submit a draft PO for approval — transitions draft → pending approval."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"PO #{po_id} not found")
    if po.status != POStatus.draft:
        raise ValueError(f"Only draft POs can be submitted. Current status: {po.status}")

    # Check if PO needs approval based on thresholds
    total = float(po.total_ngn or 0)
    required_role = "pharmacist"
    escalate = False

    thresholds = (
        db.query(ApprovalThreshold)
        .order_by(ApprovalThreshold.threshold_amount.desc())
        .all()
    )
    for t in thresholds:
        if total >= float(t.threshold_amount):
            required_role = t.required_approver_role
            escalate = t.escalate_to_owner
            break

    # Create approval record
    approval = POApproval(
        po_id=po_id,
        approver_id=submitted_by,
        status="pending",
        notes=f"Submitted by {submitted_by}. Required approver: {required_role}",
    )
    db.add(approval)
    _log_tracking_event(db, po_id, "submitted", f"Submitted by {submitted_by} for {required_role} approval")

    db.commit()
    return {
        "po_id": po_id,
        "status": "pending_approval",
        "required_approver_role": required_role,
        "escalate_to_owner": escalate,
        "total_ngn": total,
    }


def cancel_po(db: Session, po_id: int, reason: str, cancelled_by: str) -> Dict:
    """Cancel a PO that hasn't been received yet."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError(f"PO #{po_id} not found")
    if po.status in (POStatus.received, POStatus.paid):
        raise ValueError(f"Cannot cancel a {po.status} PO")

    po.status = POStatus.cancelled
    po.notes = (po.notes or "") + f"\n[CANCELLED by {cancelled_by}]: {reason}"
    _log_tracking_event(db, po_id, "cancelled", f"Cancelled by {cancelled_by}: {reason}")
    db.commit()
    return {"po_id": po_id, "status": "cancelled", "reason": reason}


def get_active_po_tracking(db: Session) -> List[Dict]:
    """Return all active POs with their latest tracking event."""
    active_statuses = [POStatus.draft, POStatus.approved, POStatus.ordered]
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.status.in_(active_statuses))
        .order_by(PurchaseOrder.created_at.desc())
        .all()
    )
    result = []
    for po in pos:
        latest_event = (
            db.query(POTracking)
            .filter_by(po_id=po.id)
            .order_by(POTracking.timestamp.desc())
            .first()
        )
        result.append({
            "po_id": po.id,
            "po_number": po.po_number,
            "vendor_id": po.vendor_id,
            "vendor_name": po.vendor.name if po.vendor else None,
            "status": po.status,
            "total_ngn": float(po.total_ngn or 0),
            "expected_delivery": po.expected_delivery.isoformat() if po.expected_delivery else None,
            "created_at": po.created_at.isoformat() if po.created_at else None,
            "latest_event": latest_event.event if latest_event else None,
            "latest_event_at": latest_event.timestamp.isoformat() if latest_event else None,
        })
    return result
