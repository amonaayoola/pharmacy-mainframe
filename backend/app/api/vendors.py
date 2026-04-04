"""
vendors.py — Phase 3: Vendor Management API
GET/POST /vendors, GET/PUT/DELETE /vendors/{id}, POST /vendors/{id}/prices
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.core.database import get_db
from app.models.models import Vendor, VendorDrugPrice, Drug
from app.services.fx_service import get_cached_fx_rate

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class VendorCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    lead_time_days: int = 3
    performance_score: float = 5.0
    is_active: bool = True


class VendorUpdate(BaseModel):
    name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    lead_time_days: Optional[int] = None
    performance_score: Optional[float] = None
    is_active: Optional[bool] = None


class VendorDrugPriceIn(BaseModel):
    drug_id: int
    unit_price_ngn: float


# ── List / Create ─────────────────────────────────────────────────────────────

@router.get("/")
def list_vendors(active_only: bool = True, db: Session = Depends(get_db)):
    q = db.query(Vendor)
    if active_only:
        q = q.filter(Vendor.is_active == True)
    vendors = q.order_by(Vendor.name).all()
    return [_vendor_out(v) for v in vendors]


@router.post("/", status_code=201)
def create_vendor(body: VendorCreate, db: Session = Depends(get_db)):
    vendor = Vendor(**body.dict())
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return _vendor_out(vendor)


# ── Single vendor ─────────────────────────────────────────────────────────────

@router.get("/{vendor_id}")
def get_vendor(vendor_id: int, db: Session = Depends(get_db)):
    vendor = _get_or_404(db, vendor_id)
    return _vendor_out(vendor, include_prices=True)


@router.put("/{vendor_id}")
def update_vendor(vendor_id: int, body: VendorUpdate, db: Session = Depends(get_db)):
    vendor = _get_or_404(db, vendor_id)
    for field, val in body.dict(exclude_unset=True).items():
        setattr(vendor, field, val)
    db.commit()
    db.refresh(vendor)
    return _vendor_out(vendor)


@router.delete("/{vendor_id}", status_code=204)
def deactivate_vendor(vendor_id: int, db: Session = Depends(get_db)):
    """Soft-delete: sets is_active=False."""
    vendor = _get_or_404(db, vendor_id)
    vendor.is_active = False
    db.commit()


# ── Drug pricing ──────────────────────────────────────────────────────────────

@router.post("/{vendor_id}/prices", status_code=201)
def set_vendor_drug_price(
    vendor_id: int,
    body: VendorDrugPriceIn,
    db: Session = Depends(get_db),
):
    """
    Upsert: create or update the unit price a vendor charges for a drug.
    Also stores the USD equivalent using the current cached FX rate.
    """
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
        existing.last_updated   = datetime.utcnow()
        db.commit()
        return _price_out(existing)

    price = VendorDrugPrice(
        vendor_id=vendor_id,
        drug_id=body.drug_id,
        unit_price_ngn=body.unit_price_ngn,
        unit_price_usd=unit_price_usd,
    )
    db.add(price)
    db.commit()
    db.refresh(price)
    return _price_out(price)


@router.get("/{vendor_id}/prices")
def list_vendor_prices(vendor_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, vendor_id)
    prices = db.query(VendorDrugPrice).filter_by(vendor_id=vendor_id).all()
    return [_price_out(p) for p in prices]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_404(db: Session, vendor_id: int) -> Vendor:
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        raise HTTPException(404, f"Vendor #{vendor_id} not found")
    return v


def _vendor_out(v: Vendor, include_prices: bool = False) -> dict:
    out = {
        "id":                v.id,
        "name":              v.name,
        "contact_person":    v.contact_person,
        "phone":             v.phone,
        "email":             v.email,
        "address":           v.address,
        "lead_time_days":    v.lead_time_days,
        "performance_score": float(v.performance_score) if v.performance_score else None,
        "is_active":         v.is_active,
        "created_at":        v.created_at,
    }
    if include_prices:
        out["prices"] = [_price_out(p) for p in v.drug_prices]
    return out


def _price_out(p: VendorDrugPrice) -> dict:
    return {
        "id":              p.id,
        "vendor_id":       p.vendor_id,
        "drug_id":         p.drug_id,
        "drug_name":       p.drug.generic_name if p.drug else None,
        "unit_price_ngn":  float(p.unit_price_ngn),
        "unit_price_usd":  float(p.unit_price_usd) if p.unit_price_usd else None,
        "last_updated":    p.last_updated,
    }
