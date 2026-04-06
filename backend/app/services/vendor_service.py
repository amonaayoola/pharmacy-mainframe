"""
Vendor Service — Phase 3A: Vendor Intelligence
Handles performance rating, category lookup, and compliance checks.
"""

import logging
from datetime import datetime, date
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Vendor, VendorDrugPrice
from app.models.procurement_models import (
    VendorCategory,
    VendorPerformance,
    VendorPricingHistory,
    VendorRelationship,
    ComplianceFlag,
)
from app.services.fx_service import get_cached_fx_rate

logger = logging.getLogger(__name__)


def rate_vendor_performance(
    db: Session,
    vendor_id: int,
    delivery_date: Optional[date],
    expected_date: Optional[date],
    quality_score: float,
    reliability_rating: float = 5.0,
    price_competitiveness: float = 5.0,
) -> Dict:
    """
    Submit or update a performance rating for a vendor after a delivery.
    Updates on_time_delivery_pct as a rolling average.
    """
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id, Vendor.is_active == True).first()
    if not vendor:
        raise ValueError(f"Vendor #{vendor_id} not found")

    # Validate scores
    for name, val in [("quality_score", quality_score), ("reliability_rating", reliability_rating), ("price_competitiveness", price_competitiveness)]:
        if not (1.0 <= val <= 5.0):
            raise ValueError(f"{name} must be between 1 and 5")

    perf = db.query(VendorPerformance).filter(VendorPerformance.vendor_id == vendor_id).first()
    if not perf:
        perf = VendorPerformance(vendor_id=vendor_id)
        db.add(perf)
        db.flush()

    # On-time delivery: 1 if on time, 0 if late (rolling 80/20 weight)
    if delivery_date and expected_date:
        on_time = 1.0 if delivery_date <= expected_date else 0.0
        current_pct = float(perf.on_time_delivery_pct or 100.0)
        # Weighted average: 80% history, 20% new reading
        perf.on_time_delivery_pct = round(current_pct * 0.8 + on_time * 100 * 0.2, 2)

    perf.quality_score = quality_score
    perf.reliability_rating = reliability_rating
    perf.price_competitiveness = price_competitiveness

    # Update vendor composite performance_score (0-10):
    # weighted: quality × 2 + reliability × 2 + price_competitiveness × 1 + on_time/10
    on_time_component = float(perf.on_time_delivery_pct or 100) / 100 * 5
    composite = (
        float(perf.quality_score) * 1.5
        + float(perf.reliability_rating) * 1.5
        + float(perf.price_competitiveness) * 1.0
        + on_time_component * 0.5
    ) / (1.5 + 1.5 + 1.0 + 0.5) * 2  # normalise to 0-10
    vendor.performance_score = round(composite, 1)

    db.commit()
    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor.name,
        "on_time_delivery_pct": float(perf.on_time_delivery_pct),
        "quality_score": float(perf.quality_score),
        "reliability_rating": float(perf.reliability_rating),
        "price_competitiveness": float(perf.price_competitiveness),
        "composite_performance_score": float(vendor.performance_score),
    }


def get_vendor_by_category(db: Session, category: str) -> List[Dict]:
    """Return all active vendors that supply the given category."""
    vendor_ids = (
        db.query(VendorCategory.vendor_id)
        .filter(VendorCategory.category.ilike(f"%{category}%"))
        .subquery()
    )
    vendors = (
        db.query(Vendor)
        .filter(Vendor.id.in_(vendor_ids), Vendor.is_active == True)
        .order_by(Vendor.performance_score.desc())
        .all()
    )
    return [_vendor_summary(db, v) for v in vendors]


def check_vendor_compliance(db: Session, vendor_id: int) -> Dict:
    """
    Check if a vendor has any active compliance flags that would block ordering.
    Returns {"compliant": bool, "flags": [...], "blocked": bool}
    """
    today = datetime.utcnow()
    flags = (
        db.query(ComplianceFlag)
        .filter(
            ComplianceFlag.vendor_id == vendor_id,
            (ComplianceFlag.expires_at == None) | (ComplianceFlag.expires_at > today),
        )
        .all()
    )
    blocked = any(f.severity == "block" for f in flags)
    return {
        "vendor_id": vendor_id,
        "compliant": not blocked,
        "blocked": blocked,
        "flags": [
            {
                "id": f.id,
                "flag_type": f.flag_type,
                "reason": f.reason,
                "severity": f.severity,
                "expires_at": f.expires_at.isoformat() if f.expires_at else None,
            }
            for f in flags
        ],
    }


def add_vendor_category(db: Session, vendor_id: int, category: str) -> Dict:
    """Add a drug category to a vendor's supply list."""
    vendor = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not vendor:
        raise ValueError(f"Vendor #{vendor_id} not found")

    existing = (
        db.query(VendorCategory)
        .filter_by(vendor_id=vendor_id, category=category)
        .first()
    )
    if existing:
        return {"vendor_id": vendor_id, "category": category, "status": "already_exists"}

    vc = VendorCategory(vendor_id=vendor_id, category=category)
    db.add(vc)
    db.commit()
    return {"vendor_id": vendor_id, "category": category, "status": "added"}


def upsert_vendor_relationship(
    db: Session,
    vendor_id: int,
    status: str,
    discount_tier: str = "standard",
    notes: Optional[str] = None,
) -> Dict:
    """Set or update the relationship status with a vendor."""
    valid_statuses = {"primary", "secondary", "suspended"}
    if status not in valid_statuses:
        raise ValueError(f"status must be one of {valid_statuses}")

    rel = db.query(VendorRelationship).filter_by(vendor_id=vendor_id).first()
    if not rel:
        rel = VendorRelationship(vendor_id=vendor_id)
        db.add(rel)

    rel.status = status
    rel.discount_tier = discount_tier
    if notes is not None:
        rel.notes = notes
    db.commit()
    return {
        "vendor_id": vendor_id,
        "status": rel.status,
        "discount_tier": rel.discount_tier,
        "notes": rel.notes,
    }


def record_pricing_history(db: Session, vendor_id: int, drug_id: int, unit_price_ngn: float) -> None:
    """Record a price quote into the historical log."""
    record = VendorPricingHistory(
        vendor_id=vendor_id,
        drug_id=drug_id,
        unit_price=unit_price_ngn,
    )
    db.add(record)
    db.flush()


def get_pricing_history(db: Session, vendor_id: int, drug_id: int, limit: int = 20) -> List[Dict]:
    """Return historical price quotes for a vendor/drug pair."""
    records = (
        db.query(VendorPricingHistory)
        .filter_by(vendor_id=vendor_id, drug_id=drug_id)
        .order_by(VendorPricingHistory.quoted_date.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "unit_price_ngn": float(r.unit_price),
            "quoted_date": r.quoted_date.isoformat(),
        }
        for r in records
    ]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _vendor_summary(db: Session, v: Vendor) -> Dict:
    perf = db.query(VendorPerformance).filter_by(vendor_id=v.id).first()
    rel = db.query(VendorRelationship).filter_by(vendor_id=v.id).first()
    cats = db.query(VendorCategory).filter_by(vendor_id=v.id).all()
    return {
        "id": v.id,
        "name": v.name,
        "email": v.email,
        "phone": v.phone,
        "lead_time_days": v.lead_time_days,
        "performance_score": float(v.performance_score) if v.performance_score else None,
        "is_active": v.is_active,
        "categories": [c.category for c in cats],
        "relationship_status": rel.status if rel else None,
        "discount_tier": rel.discount_tier if rel else None,
        "on_time_delivery_pct": float(perf.on_time_delivery_pct) if perf else None,
        "quality_score": float(perf.quality_score) if perf else None,
    }
