"""
vendors.py — Phase 3A: Vendor Intelligence API
Full vendor management with performance ratings, categories, relationships, pricing history.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, datetime

from app.core.database import get_db
from app.models.models import Vendor, VendorDrugPrice, Drug
from app.models.procurement_models import (
    VendorCategory, VendorPerformance, VendorPricingHistory, VendorRelationship
)
from app.services.fx_service import get_cached_fx_rate
from app.services import vendor_service

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class VendorCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    lead_time_days: int = 3
    min_order_qty: Optional[int] = None
    categories: Optional[List[str]] = []
    contact_person: Optional[str] = None


class VendorUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    lead_time_days: Optional[int] = None
    min_order_qty: Optional[int] = None
    is_active: Optional[bool] = None
    contact_person: Optional[str] = None


class PerformanceRatingIn(BaseModel):
    delivery_date: Optional[date] = None
    expected_date: Optional[date] = None
    quality_score: float             # 1-5
    reliability_rating: float = 5.0  # 1-5
    price_competitiveness: float = 5.0  # 1-5


class VendorRelationshipIn(BaseModel):
    status: str         # primary / secondary / suspended
    discount_tier: str = "standard"
    notes: Optional[str] = None


class VendorDrugPriceIn(BaseModel):
    drug_id: int
    unit_price_ngn: float


# ── List / Create ─────────────────────────────────────────────────────────────

@router.get("/")
def list_vendors(
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active == True)
    vendors = q.order_by(Vendor.name).all()
    return [_vendor_out(db, v) for v in vendors]


@router.post("/", status_code=201)
def create_vendor(body: VendorCreate, db: Session = Depends(get_db)):
    vendor = Vendor(
        name=body.name,
        email=body.email,
        phone=body.phone,
        address=body.address,
        lead_time_days=body.lead_time_days,
        contact_person=body.contact_person,
    )
    db.add(vendor)
    db.flush()

    # Add categories
    for cat in (body.categories or []):
        if cat:
            db.add(VendorCategory(vendor_id=vendor.id, category=cat))

    db.commit()
    db.refresh(vendor)
    return _vendor_out(db, vendor)


# ── Single vendor ─────────────────────────────────────────────────────────────

@router.get("/search")
def search_vendors_by_category(
    category: str = Query(..., description="Drug category, e.g. 'antibiotics'"),
    db: Session = Depends(get_db),
):
    """Find all active vendors that supply a given drug category."""
    return vendor_service.get_vendor_by_category(db, category)


@router.get("/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    vendor = _get_or_404(db, vendor_id)
    return _vendor_out(db, vendor, include_prices=True, include_performance=True)


@router.patch("/{vendor_id}")
def update_vendor(vendor_id: int, body: VendorUpdate, db: Session = Depends(get_db)):
    vendor = _get_or_404(db, vendor_id)
    for field, val in body.dict(exclude_unset=True).items():
        if hasattr(vendor, field):
            setattr(vendor, field, val)
    db.commit()
    db.refresh(vendor)
    return _vendor_out(db, vendor)


# ── Performance rating ────────────────────────────────────────────────────────

@router.post("/{vendor_id}/rate")
def rate_vendor(vendor_id: int, body: PerformanceRatingIn, db: Session = Depends(get_db)):
    """Submit a performance rating for a vendor after a delivery."""
    _get_or_404(db, vendor_id)
    try:
        return vendor_service.rate_vendor_performance(
            db,
            vendor_id=vendor_id,
            delivery_date=body.delivery_date,
            expected_date=body.expected_date,
            quality_score=body.quality_score,
            reliability_rating=body.reliability_rating,
            price_competitiveness=body.price_competitiveness,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Relationship management ───────────────────────────────────────────────────

@router.post("/{vendor_id}/relationship")
def set_vendor_relationship(
    vendor_id: int,
    body: VendorRelationshipIn,
    db: Session = Depends(get_db),
):
    """Set vendor relationship status (primary / secondary / suspended)."""
    _get_or_404(db, vendor_id)
    try:
        return vendor_service.upsert_vendor_relationship(
            db, vendor_id, body.status, body.discount_tier, body.notes
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Category management ───────────────────────────────────────────────────────

@router.post("/{vendor_id}/categories")
def add_category(vendor_id: int, category: str = Query(...), db: Session = Depends(get_db)):
    """Add a drug category to a vendor's supply capabilities."""
    _get_or_404(db, vendor_id)
    try:
        return vendor_service.add_vendor_category(db, vendor_id, category)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{vendor_id}/categories/{category}", status_code=204)
def remove_category(vendor_id: int, category: str, db: Session = Depends(get_db)):
    """Remove a category from a vendor."""
    _get_or_404(db, vendor_id)
    vc = db.query(VendorCategory).filter_by(vendor_id=vendor_id, category=category).first()
    if vc:
        db.delete(vc)
        db.commit()


# ── Drug pricing ──────────────────────────────────────────────────────────────

@router.post("/{vendor_id}/prices", status_code=201)
def set_vendor_drug_price(
    vendor_id: int,
    body: VendorDrugPriceIn,
    db: Session = Depends(get_db),
):
    """Upsert the unit price a vendor charges for a drug (stores NGN + USD equiv)."""
    _get_or_404(db, vendor_id)
    drug = db.query(Drug).filter(Drug.id == body.drug_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")

    fx_rate = get_cached_fx_rate()
    unit_price_usd = round(body.unit_price_ngn / fx_rate, 4) if fx_rate else None

    existing = (
        db.query(VendorDrugPrice)
        .filter_by(vendor_id=vendor_id, drug_id=body.drug_id)
        .first()
    )
    if existing:
        existing.unit_price_ngn = body.unit_price_ngn
        existing.unit_price_usd = unit_price_usd
        existing.last_updated = datetime.utcnow()
    else:
        existing = VendorDrugPrice(
            vendor_id=vendor_id,
            drug_id=body.drug_id,
            unit_price_ngn=body.unit_price_ngn,
            unit_price_usd=unit_price_usd,
        )
        db.add(existing)

    # Record in pricing history
    vendor_service.record_pricing_history(db, vendor_id, body.drug_id, body.unit_price_ngn)

    db.commit()
    return _price_out(existing)


@router.get("/{vendor_id}/prices")
def list_vendor_prices(vendor_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, vendor_id)
    prices = db.query(VendorDrugPrice).filter_by(vendor_id=vendor_id).all()
    return [_price_out(p) for p in prices]


@router.get("/{vendor_id}/prices/{drug_id}/history")
def get_price_history(vendor_id: int, drug_id: int, limit: int = 20, db: Session = Depends(get_db)):
    """Get historical pricing for a vendor/drug pair."""
    _get_or_404(db, vendor_id)
    return vendor_service.get_pricing_history(db, vendor_id, drug_id, limit)


@router.delete("/{vendor_id}", status_code=204)
def deactivate_vendor(vendor_id: int, db: Session = Depends(get_db)):
    """Soft-delete: sets is_active=False."""
    vendor = _get_or_404(db, vendor_id)
    vendor.is_active = False
    db.commit()


@router.get("/{vendor_id}/compliance")
def vendor_compliance(vendor_id: int, db: Session = Depends(get_db)):
    """Check compliance status for a vendor."""
    _get_or_404(db, vendor_id)
    return vendor_service.check_vendor_compliance(db, vendor_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_404(db: Session, vendor_id: int) -> Vendor:
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(404, f"Vendor #{vendor_id} not found")
    return v


def _vendor_out(db: Session, v: Vendor, include_prices: bool = False, include_performance: bool = False) -> dict:
    cats = db.query(VendorCategory).filter_by(vendor_id=v.id).all()
    rel = db.query(VendorRelationship).filter_by(vendor_id=v.id).first()
    out = {
        "id": v.id,
        "name": v.name,
        "email": v.email,
        "phone": v.phone,
        "address": v.address,
        "lead_time_days": v.lead_time_days,
        "performance_score": float(v.performance_score) if v.performance_score else None,
        "is_active": v.is_active,
        "categories": [c.category for c in cats],
        "relationship_status": rel.status if rel else None,
        "discount_tier": rel.discount_tier if rel else None,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    }
    if include_prices:
        out["prices"] = [_price_out(p) for p in v.drug_prices]
    if include_performance:
        perf = db.query(VendorPerformance).filter_by(vendor_id=v.id).first()
        out["performance"] = {
            "on_time_delivery_pct": float(perf.on_time_delivery_pct) if perf else None,
            "quality_score": float(perf.quality_score) if perf else None,
            "reliability_rating": float(perf.reliability_rating) if perf else None,
            "price_competitiveness": float(perf.price_competitiveness) if perf else None,
        } if perf else None
    return out


def _price_out(p: VendorDrugPrice) -> dict:
    return {
        "id": p.id,
        "vendor_id": p.vendor_id,
        "drug_id": p.drug_id,
        "drug_name": p.drug.generic_name if p.drug else None,
        "unit_price_ngn": float(p.unit_price_ngn),
        "unit_price_usd": float(p.unit_price_usd) if p.unit_price_usd else None,
        "last_updated": p.last_updated.isoformat() if p.last_updated else None,
    }
