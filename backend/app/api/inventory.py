"""inventory.py — Phase 1C: Smart Inventory System"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta

from app.core.database import get_db
from app.models.models import StockBatch, StockStatus, Drug, NAFDACStatus
from app.services.inventory_service import (
    get_demand_forecast,
    get_inventory_alerts,
    list_auto_generated_pos,
    trigger_auto_reorder,
)

router = APIRouter()

class BatchCreate(BaseModel):
    drug_id: int
    batch_no: str
    quantity: int
    unit_cost_usd: Optional[float] = None
    expiry_date: date
    manufacture_date: Optional[date] = None
    location: str = "Main Shelf"

@router.get("/")
def list_stock(db: Session = Depends(get_db)):
    today = date.today()
    batches = (
        db.query(StockBatch)
        .join(Drug)
        .filter(Drug.is_active == True)
        .order_by(StockBatch.expiry_date.asc())
        .all()
    )
    result = []
    for b in batches:
        days_to_exp = (b.expiry_date - today).days
        result.append({
            "batch_id": b.id,
            "drug_id": b.drug_id,
            "brand_name": b.drug.brand_name,
            "generic_name": b.drug.generic_name,
            "batch_no": b.batch_no,
            "quantity": b.quantity,
            "expiry_date": b.expiry_date,
            "days_to_expiry": days_to_exp,
            "status": b.status,
            "nafdac_status": b.nafdac_status,
            "location": b.location,
            "cost_usd": float(b.unit_cost_usd) if b.unit_cost_usd else float(b.drug.cost_usd),
        })
    return result

@router.get("/low-stock")
def low_stock_alerts(threshold_days: int = 7, db: Session = Depends(get_db)):
    """Items where days of stock remaining < threshold."""
    # In production, join with sales velocity table
    return {
        "threshold_days": threshold_days,
        "items": [],
        "message": "Connect to sales_velocity view for live burn rates"
    }

@router.get("/expiring")
def expiring_stock(days: int = 90, db: Session = Depends(get_db)):
    today = date.today()
    cutoff = today + timedelta(days=days)
    batches = (
        db.query(StockBatch)
        .filter(
            StockBatch.expiry_date <= cutoff,
            StockBatch.expiry_date >= today,
            StockBatch.quantity > 0,
        )
        .order_by(StockBatch.expiry_date.asc())
        .all()
    )
    return [
        {
            "batch_no": b.batch_no,
            "drug": b.drug.brand_name,
            "quantity": b.quantity,
            "expiry_date": b.expiry_date,
            "days_left": (b.expiry_date - today).days,
            "status": b.status,
        }
        for b in batches
    ]

@router.post("/batches", status_code=201)
def receive_batch(batch_in: BatchCreate, db: Session = Depends(get_db)):
    drug = db.query(Drug).filter(Drug.id == batch_in.drug_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")
    existing = db.query(StockBatch).filter(StockBatch.batch_no == batch_in.batch_no).first()
    if existing:
        raise HTTPException(400, f"Batch {batch_in.batch_no} already exists")
    batch = StockBatch(
        drug_id=batch_in.drug_id,
        batch_no=batch_in.batch_no,
        quantity=batch_in.quantity,
        unit_cost_usd=batch_in.unit_cost_usd or drug.cost_usd,
        expiry_date=batch_in.expiry_date,
        manufacture_date=batch_in.manufacture_date,
        location=batch_in.location,
        nafdac_status=NAFDACStatus.pending,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return {"batch_id": batch.id, "batch_no": batch.batch_no, "message": "Batch received. Verify with NAFDAC."}


# ── Phase 1C: Smart Inventory System ─────────────────────────────────────────

@router.get("/forecast")
def demand_forecast(db: Session = Depends(get_db)):
    """
    30-day demand predictions for all active drugs.

    Analyses the past 90 days of dispensing records to calculate velocity
    (units/day), identifies chronic refill patterns, and projects the next
    30-day demand. Also returns the Economic Order Quantity (EOQ) for each drug.

    Returns results sorted by velocity descending (fastest movers first).
    """
    forecast = get_demand_forecast(db)
    return {
        "forecast_window_days":  90,
        "forecast_horizon_days": 30,
        "drug_count":            len(forecast),
        "drugs":                 forecast,
    }


@router.get("/alerts")
def inventory_alerts(db: Session = Depends(get_db)):
    """
    Consolidated inventory alert dashboard.

    Returns three alert buckets:
      - low_stock:   drugs with < 5-day supply remaining (based on live velocity)
      - slow_movers: velocity < 0.5 units/day and not a chronic refill drug
      - expiring:    stock batches with < 90 days to expiry

    Each entry includes actionable metadata (recommended EOQ, alert level, etc.).
    """
    return get_inventory_alerts(db)


@router.post("/auto-reorder", status_code=201)
def auto_reorder(db: Session = Depends(get_db)):
    """
    Trigger automatic purchase order generation for fast-movers below the
    5-day supply threshold.

    Rules:
      - Only fast-movers (velocity > 0) with < 5 days of supply are eligible.
      - Slow movers (< 0.5/day) that are not chronic receive a manual-review
        flag instead of an auto-PO.
      - Drugs with an existing pending auto-PO are skipped (no duplicates).
      - Order quantity is calculated via the Wilson EOQ formula.
      - POs are created as *draft* (status=draft, auto_generated=True) and
        require pharmacist approval before being sent to the wholesaler.

    Returns a summary of all POs created plus any critical flags.
    """
    result = trigger_auto_reorder(db)
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result["message"])
    return result
