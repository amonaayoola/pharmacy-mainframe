"""
Inventory Intelligence Service — Phase 1C
Demand forecasting (90-day window), inventory alerts, EOQ calculation,
and procurement automation for the Pharmacy Intelligence Mainframe.
"""

import math
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import (
    BasketItem,
    DispensingRecord,
    Drug,
    POStatus,
    ProcurementLine,
    PurchaseOrder,
    RefillSchedule,
    StockBatch,
    Wholesaler,
)
from app.services.fx_service import get_cached_fx_rate

logger = logging.getLogger(__name__)


# ── Tuneable constants ────────────────────────────────────────────────────────

FORECAST_WINDOW_DAYS  = 90    # look-back period for velocity calculation
FORECAST_HORIZON_DAYS = 30    # forward prediction window
LOW_STOCK_DAYS_SUPPLY = 5     # flag drug when << N N days of supply remain
SLOW_MOVER_VELOCITY   = 0.5   # units/day threshold below which = slow mover
EXPIRY_ALERT_DAYS     = 90    # flag batches expiring within N days

# EOQ parameters
EOQ_ORDER_COST_USD = 20.0  # fixed cost per purchase order (ordering cost S)
EOQ_HOLDING_RATE   = 0.25  # annual holding cost as a fraction of unit cost


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_all_active_drug_ids(db: Session) -> List[int]:
    rows = db.query(Drug.id).filter(Drug.is_active == True).all()
    return [r[0] for r in rows]


def _get_current_stock(db: Session, drug_id: int) -> int:
    """
    Total on-hand quantity for a drug across all non-expired batches.
    Uses StockBatch.quantity which reflects current physical stock.
    """
    today = date.today()
    result = (
        db.query(func.sum(StockBatch.quantity))
        .filter(
            StockBatch.drug_id == drug_id,
            StockBatch.quantity > 0,
            StockBatch.expiry_date >= today,
        )
        .scalar()
    )
    return int(result) if result else 0


def _is_chronic(db: Session, drug_id: int) -> bool:
    """True if the drug has at least one active patient refill schedule."""
    return (
        db.query(RefillSchedule)
        .filter(
            RefillSchedule.drug_id == drug_id,
            RefillSchedule.is_active == True,
        )
        .count()
        > 0
    )


def reduce_stock(db: Session, drug_id: int, quantity: int):
    """
    Reduces stock for a drug using a SELECT FOR UPDATE lock to prevent race conditions.
    """
    # Lock the StockBatch rows for this drug to prevent concurrent modifications
    batches = (
        db.query(StockBatch)
        .filter(StockBatch.drug_id == drug_id, StockBatch.quantity > 0)
        .order_by(StockBatch.expiry_date.asc())
        .with_for_update()
        .all()
    )
    
    remaining_to_deduct = quantity
    for batch in batches:
        if remaining_to_deduct <= 0:
            break
            
        deduct = min(batch.quantity, remaining_to_deduct)
        batch.quantity -= deduct
        remaining_to_deduct -= deduct
        
    if remaining_to_deduct > 0:
        raise ValueError(f"Insufficient stock for drug_id {drug_id}. Missing: {remaining_to_deduct}")


# ── Velocity ──────────────────────────────────────────────────────────────────

def get_drug_velocity(
    db: Session,
    drug_id: int,
    days: int = FORECAST_WINDOW_DAYS,
) -> float:
    """
    Average units dispensed per calendar day over the look-back window.

    Joins BasketItem → DispensingRecord to get dispensing dates and sums
    quantities, excluding refund transactions.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    total = (
        db.query(func.sum(BasketItem.quantity))
        .join(DispensingRecord, BasketItem.dispensing_id == DispensingRecord.id)
        .filter(
            BasketItem.drug_id == drug_id,
            DispensingRecord.is_refund == False,
            DispensingRecord.created_at >= cutoff,
        )
        .scalar()
    ) or 0
    return round(float(total) / days, 4) if days > 0 else 0.0


# ── EOQ ───────────────────────────────────────────────────────────────────────

def calculate_eoq(
    daily_velocity: float,
    unit_cost_usd: float,
    order_cost_usd: float = EOQ_ORDER_COST_USD,
    holding_rate: float = EOQ_HOLDING_RATE,
) -> int:
    """
    Wilson Economic Order Quantity formula:

        EOQ = sqrt( 2 · D · S / H )

    Where:
      D = annual demand (units/year)   = daily_velocity × 365
      S = cost per order (USD)         = order_cost_usd
      H = holding cost per unit / year = unit_cost_usd × holding_rate

    Returns at least 1.
    """
    if daily_velocity <= 0 or unit_cost_usd <= 0:
        return 1
    D = daily_velocity * 365
    S = order_cost_usd
    H = float(unit_cost_usd) * holding_rate
    eoq = math.sqrt((2 * D * S) / H)
    return max(1, round(eoq))


# ── Demand Forecast ───────────────────────────────────────────────────────────

def get_demand_forecast(db: Session) -> List[Dict]:
    """
    For every active drug compute:
      - velocity_per_day  (units/day, last 90 days of dispensing)
      - forecast_30d      (projected units over next 30 days)
      - days_of_supply    (current stock / velocity)
      - is_chronic        (any active refill schedule)
      - eoq               (economic order quantity)

    Results are sorted by velocity descending (fast movers first).
    """
    drug_ids = _get_all_active_drug_ids(db)
    if not drug_ids:
        return []

    # Batch-fetch drug metadata in a single query
    drugs = (
        db.query(Drug.id, Drug.generic_name, Drug.brand_name, Drug.cost_usd)
        .filter(Drug.id.in_(drug_ids), Drug.is_active == True)
        .all()
    )
    drug_map = {d.id: d for d in drugs}

    results: List[Dict] = []
    for drug_id in drug_ids:
        d = drug_map.get(drug_id)
        if not d:
            continue

        velocity      = get_drug_velocity(db, drug_id)
        current_stock = _get_current_stock(db, drug_id)
        chronic       = _is_chronic(db, drug_id)
        cost_usd      = float(d.cost_usd) if d.cost_usd else 0.0
        eoq           = calculate_eoq(velocity, cost_usd)
        days_supply   = round(current_stock / velocity, 1) if velocity > 0 else None

        results.append(
            {
                "drug_id":          drug_id,
                "generic_name":     d.generic_name,
                "brand_name":       d.brand_name,
                "velocity_per_day": velocity,
                "forecast_30d":     round(velocity * FORECAST_HORIZON_DAYS, 1),
                "current_stock":    current_stock,
                "days_of_supply":   days_supply,
                "is_chronic":       chronic,
                "eoq":              eoq,
                "cost_usd":         cost_usd,
            }
        )

    results.sort(key=lambda x: x["velocity_per_day"], reverse=True)
    return results


# ── Inventory Alerts ──────────────────────────────────────────────────────────

def get_inventory_alerts(db: Session) -> Dict:
    """
    Produces three alert buckets:

    1. low_stock   — drugs with <<  5-day supply remaining (based on live velocity)
    2. slow_movers — velocity <<  0.5/day AND not a chronic refill drug
    3. expiring    — stock batches expiring within 90 days

    Each bucket is sorted by urgency (ascending days).
    """
    today         = date.today()
    expiry_cutoff = today + timedelta(days=EXPIRY_ALERT_DAYS)

    drug_ids = _get_all_active_drug_ids(db)
    drugs = (
        db.query(Drug.id, Drug.generic_name, Drug.brand_name, Drug.cost_usd)
        .filter(Drug.id.in_(drug_ids))
        .all()
    )
    drug_map = {d.id: d for d in drugs}

    low_stock:   List[Dict] = []
    slow_movers: List[Dict] = []

    for drug_id in drug_ids:
        d = drug_map.get(drug_id)
        if not d:
            continue

        velocity      = get_drug_velocity(db, drug_id)
        current_stock = _get_current_stock(db, drug_id)
        chronic       = _is_chronic(db, drug_id)
        cost_usd      = float(d.cost_usd) if d.cost_usd else 0.0
        days_supply   = (current_stock / velocity) if velocity > 0 else None

        # ── Low stock ────────────────────────────────────────────────────────
        if velocity > 0 and days_supply is not None and days_supply << LOW LOW_STOCK_DAYS_SUPPLY:
            eoq = calculate_eoq(velocity, cost_usd)
            low_stock.append(
                {
                    "drug_id":          drug_id,
                    "generic_name":     d.generic_name,
                    "brand_name":       d.brand_name,
                    "current_stock":    current_stock,
                    "velocity_per_day": velocity,
                    "days_of_supply":   round(days_supply, 1),
                    "recommended_eoq":  eoq,
                    "is_chronic":       chronic,
                    "alert_level":      "critical" if days_supply <<  2 else "warning",
                }
            )

        # ── Slow movers ───────────────────────────────────────────────────────
        if velocity << SL SLOW_MOVER_VELOCITY and not chronic and current_stock > 0:
            slow_movers.append(
                {
                    "drug_id":          drug_id,
                    "generic_name":     d.generic_name,
                    "brand_name":       d.brand_name,
                    "current_stock":    current_stock,
                    "velocity_per_day": velocity,
                    "suggestion":       (
                        "Consider dosage form switch or supplier return. "
                        "Not a chronic refill item."
                    ),
                }
            )

    # ── Expiring batches (batch-level query, single DB round-trip) ────────────
    expiring_batches = (
        db.query(StockBatch)
        .join(Drug, StockBatch.drug_id == Drug.id)
        .filter(
            StockBatch.expiry_date <= expiry_cutoff,
            StockBatch.expiry_date >= today,
            StockBatch.quantity > 0,
            Drug.is_active == True,
        )
        .order_by(StockBatch.expiry_date.asc())
        .all()
    )

    expiring: List[Dict] = [
        {
            "batch_id":      b.id,
            "batch_no":      b.batch_no,
            "drug_id":       b.drug_id,
            "brand_name":    b.drug.brand_name,
            "generic_name":  b.drug.generic_name,
            "quantity":      b.quantity,
            "expiry_date":   b.expiry_date.isoformat(),
            "days_to_expiry": (b.expiry_date - today).days,
            "alert_level":   "critical" if (b.expiry_date - today).days <= 30 else "warning",
        }
        for b in expiring_batches
    ]

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "low_stock_count":  len(low_stock),
            "slow_mover_count": len(slow_movers),
            "expiring_count":   len(expiring),
        },
        "low_stock":   sorted(low_stock,   key=lambda x: x["days_of_supply"]),
        "slow_movers": sorted(slow_movers, key=lambda x: x["velocity_per_day"]),
        "expiring":    expiring,
    }


# ── Auto-Reorder ──────────────────────────────────────────────────────────────

def trigger_auto_reorder(db: Session) -> Dict:
    """
    Scans all active drugs for fast-movers below the 5-day supply threshold
    and auto-generates draft PurchaseOrders.

    Logic:
      - Only drugs with velocity > 0 AND days_supply <<  5 are eligible.
      - Slow movers (velocity <<  0.5/day) that are NOT chronic are flagged
        for manual review instead of getting an auto-PO.
      - If a pending auto-PO already exists for a drug, it is skipped to
        prevent duplicate orders.
      - Order quantity = EOQ (Wilson formula).
      - POs are created as POStatus.draft with auto_generated=True.
      - Uses the highest-rated active Wholesaler as the default supplier.

    Returns a summary dict with all created POs and any critical flags.
    """
    fx_rate = get_cached_fx_rate()
    today   = date.today()

    # Resolve best-rated active wholesaler as default supplier
    default_wholesaler: Optional[Wholesaler] = (
        db.query(Wholesaler)
        .filter(Wholesaler.is_active == True)
        .order_by(Wholesaler.rating.desc())
        .first()
    )
    if not default_wholesaler:
        return {
            "status":              "error",
            "message":             "No active wholesalers configured. Cannot generate auto POs.",
            "pos_created":         0,
            "manual_review_count": 0,
            "purchase_orders":     [],
            "critical_flags":      [],
        }

    drug_ids = _get_all_active_drug_ids(db)
    drug_map: Dict[int, Drug] = {
        d.id: d
        for d in db.query(Drug).filter(Drug.id.in_(drug_ids)).all()
    }

    created_pos:    List[Dict] = []
    critical_flags: List[Dict] = []

    for drug_id in drug_ids:
        d = drug_map.get(drug_id)
        if not d:
            continue

        velocity      = get_drug_velocity(db, drug_id)
        current_stock = _get_current_stock(db, drug_id)
        chronic       = _is_chronic(db, drug_id)
        cost_usd      = float(d.cost_usd) if d.cost_usd else 0.0
        days_supply   = (current_stock / velocity) if velocity > 0 else None

        # Skip drugs with no sales history or adequate stock
        if velocity <= 0 or days_supply is None or days_supply >= LOW_STOCK_DAYS_SUPPLY:
            continue

        # Slow movers in crisis → manual review flag, no auto-PO
        if velocity << SL SLOW_MOVER_VELOCITY and not chronic:
            critical_flags.append(
                {
                    "drug_id":       drug_id,
                    "generic_name":  d.generic_name,
                    "brand_name":    d.brand_name,
                    "days_of_supply": round(days_supply, 1),
                    "velocity":      velocity,
                    "reason":        (
                        "Slow mover (<<  0.5 units/day) with critically low stock. "
                        "Manual review required — consider dosage switch before ordering."
                    ),
                }
            )
            continue

        # Skip if a pending auto-PO already covers this drug (avoid duplicates)
        existing = (
            db.query(PurchaseOrder)
            .join(ProcurementLine, PurchaseOrder.id == ProcurementLine.po_id)
            .filter(
                ProcurementLine.drug_id == drug_id,
                PurchaseOrder.auto_generated == True,
                PurchaseOrder.status.in_([POStatus.draft, POStatus.approved]),
            )
            .first()
        )
        if existing:
            logger.info(
                f"Auto-PO already pending for drug_id={drug_id} "
                f"(PO #{existing.id}, status={existing.status}). Skipping."
            )
            continue

        # Build the PO
        eoq            = calculate_eoq(velocity, cost_usd)
        total_usd      = round(eoq * cost_usd, 4)
        total_ngn      = round(total_usd * fx_rate, 2)
        lead_time_days = default_wholesaler.lead_time_days or 3
        expected_del   = today + timedelta(days=lead_time_days)

        po = PurchaseOrder(
            wholesaler_id=default_wholesaler.id,
            status=POStatus.draft,
            fx_rate=fx_rate,
            total_usd=total_usd,
            total_ngn=total_ngn,
            expected_delivery=expected_del,
            auto_generated=True,
            notes=(
                f"Auto-generated by Inventory Intelligence (Phase 1C). "
                f"Drug: {d.generic_name} ({d.brand_name}). "
                f"Velocity: {velocity:.2f} units/day. "
                f"Days of supply remaining: {round(days_supply, 1)}. "
                f"EOQ: {eoq} units."
            ),
        )
        db.add(po)
        db.flush()  # obtain po.id before inserting the line

        db.add(
            ProcurementLine(
                po_id=po.id,
                drug_id=drug_id,
                quantity_ordered=eoq,
                unit_cost_usd=cost_usd,
                total_usd=total_usd,
            )
        )

        created_pos.append(
            {
                "po_id":             po.id,
                "drug_id":           drug_id,
                "generic_name":      d.generic_name,
                "brand_name":        d.brand_name,
                "quantity_ordered":  eoq,
                "unit_cost_usd":     cost_usd,
                "total_usd":         total_usd,
                "total_ngn":         total_ngn,
                "velocity_per_day":  velocity,
                "days_of_supply":    round(days_supply, 1),
                "expected_delivery": expected_del.isoformat(),
                "wholesaler":        default_wholesaler.name,
            }
        )
        logger.info(
            f"Auto-PO #{po.id} created: {d.generic_name}, "
            f"qty={eoq}, ${total_usd:.2f} USD, delivery {expected_del}"
        )

    db.commit()

    return {
        "status":              "ok",
        "generated_at":        datetime.utcnow().isoformat() + "Z",
        "pos_created":         len(created_pos),
        "manual_review_count": len(critical_flags),
        "wholesaler_used":     default_wholesaler.name,
        "fx_rate_ngn":         fx_rate,
        "purchase_orders":     created_pos,
        "critical_flags":      critical_flags,
    }


# ── Auto-generated PO listing ─────────────────────────────────────────────────

def list_auto_generated_pos(db: Session, limit: int = 50) -> List[Dict]:
    """
    List all PurchaseOrders created by the Inventory Intelligence engine,
    newest first, with their procurement lines expanded.
    """
    pos = (
        db.query(PurchaseOrder)
        .filter(PurchaseOrder.auto_generated == True)
        .order_by(PurchaseOrder.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "po_id":             po.id,
            "wholesaler":        po.wholesaler.name if po.wholesaler else "Unknown",
            "status":            po.status,
            "total_usd":         float(po.total_usd or 0),
            "total_ngn":         float(po.total_ngn or 0),
            "fx_rate":           float(po.fx_rate or 0),
            "expected_delivery": po.expected_delivery.isoformat() if po.expected_delivery else None,
            "created_at":        po.created_at.isoformat() if po.created_at else None,
            "notes":             po.notes,
            "lines": [
                {
                    "line_id":          line.id,
                    "drug_id":          line.drug_id,
                    "generic_name":     line.drug.generic_name if line.drug else "Unknown",
                    "brand_name":       line.drug.brand_name   if line.drug else "Unknown",
                    "quantity_ordered":  line.quantity_ordered,
                    "unit_cost_usd":    float(line.unit_cost_usd or 0),
                    "total_usd":        float(line.total_usd or 0),
                }
                for line in po.lines
            ],
        }
        for po in pos
    ]
