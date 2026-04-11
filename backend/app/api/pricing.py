"""pricing.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.core.auth import require_admin
from app.models.models import Drug, FXRate, FXAlert
from app.services.fx_service import PricingEngine, get_cached_fx_rate, fetch_live_fx_rate, set_manual_fx_rate

router = APIRouter()

class PriceCalcRequest(BaseModel):
    cost_usd: float
    margin: Optional[float] = 0.25
    fx_rate: Optional[float] = None

@router.post("/calculate")
def calculate_price(req: PriceCalcRequest):
    engine = PricingEngine(fx_rate=req.fx_rate or get_cached_fx_rate(), margin=req.margin)
    return {
        "cost_usd": req.cost_usd,
        "fx_rate": engine.fx_rate,
        "margin_pct": req.margin * 100,
        "landed_ngn": engine.landed_cost_ngn(req.cost_usd),
        "retail_ngn": engine.retail_price_ngn(req.cost_usd),
        "margin_ngn": engine.margin_amount_ngn(req.cost_usd),
    }

@router.get("/all-drugs")
def price_all_drugs(margin: Optional[float] = 0.25, db: Session = Depends(get_db)):
    drugs = db.query(Drug).filter(Drug.is_active == True).all()
    engine = PricingEngine(fx_rate=get_cached_fx_rate(), margin=margin)
    return {
        "fx_rate": engine.fx_rate,
        "margin_pct": margin * 100,
        "prices": engine.price_all_drugs(drugs, margin),
    }

@router.get("/fx-rate")
async def get_fx_rate():
    rate = await fetch_live_fx_rate()
    return {"usd_ngn": rate, "source": "AbokiFX"}

@router.post("/fx-rate/manual")
def set_fx_rate(rate: float, current_user: dict = Depends(require_admin)):
    set_manual_fx_rate(rate)
    return {"message": f"FX rate manually set to {rate:,.2f}", "usd_ngn": rate}

@router.get("/fx-history")
def fx_history(limit: int = 30, db: Session = Depends(get_db)):
    records = db.query(FXRate).order_by(FXRate.recorded_at.desc()).limit(limit).all()
    return [{"rate": float(r.usd_ngn), "source": r.source, "at": r.recorded_at} for r in records]

@router.get("/fx-alerts")
def fx_alerts(limit: int = 20, db: Session = Depends(get_db)):
    """
    Returns Claude-generated volatility alerts from the fx_alerts table.
    Triggered automatically when the Naira swings >= FX_VOLATILITY_THRESHOLD_PCT.
    All data lives in your PostgreSQL database — no external service needed.
    """
    alerts = (
        db.query(FXAlert)
        .order_by(FXAlert.triggered_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": a.id,
            "prev_rate": float(a.prev_rate),
            "new_rate": float(a.new_rate),
            "change_pct": float(a.change_pct),
            "direction": a.direction,
            "claude_analysis": a.claude_analysis,
            "drugs_affected_count": a.drugs_affected_count,
            "model_used": a.model_used,
            "triggered_at": a.triggered_at,
        }
        for a in alerts
    ]

@router.get("/fx-alerts/latest")
def fx_alert_latest(db: Session = Depends(get_db)):
    """Returns the single most recent volatility alert — useful for dashboard banners."""
    alert = db.query(FXAlert).order_by(FXAlert.triggered_at.desc()).first()
    if not alert:
        return {"alert": None, "message": "No volatility alerts recorded yet."}
    return {
        "id": alert.id,
        "prev_rate": float(alert.prev_rate),
        "new_rate": float(alert.new_rate),
        "change_pct": float(alert.change_pct),
        "direction": alert.direction,
        "claude_analysis": alert.claude_analysis,
        "drugs_affected_count": alert.drugs_affected_count,
        "triggered_at": alert.triggered_at,
    }

