"""
Procurement Service — Phase 3: Auto-Procurement Intelligence
Vendor selection, PO lifecycle, budget controls, NAFDAC checks, FX integration.
"""

import logging
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
