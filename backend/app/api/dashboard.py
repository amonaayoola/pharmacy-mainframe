"""dashboard.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date, timedelta
from app.core.database import get_db
from app.models.models import (
    DispensingRecord, StockBatch, RefillSchedule,
    PurchaseOrder, POStatus, StockStatus
)
from app.services.fx_service import get_cached_fx_rate

router = APIRouter()

@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    today = date.today()
    yesterday = today - timedelta(days=1)
    today_sales = float(db.query(func.coalesce(func.sum(DispensingRecord.total_ngn), 0))
        .filter(func.date(DispensingRecord.created_at) == today, DispensingRecord.is_refund == False).scalar() or 0)
    yesterday_sales = float(db.query(func.coalesce(func.sum(DispensingRecord.total_ngn), 0))
        .filter(func.date(DispensingRecord.created_at) == yesterday, DispensingRecord.is_refund == False).scalar() or 1)
    low_stock = db.query(StockBatch).filter(StockBatch.status.in_([StockStatus.low, StockStatus.critical])).count()
    expiring = db.query(StockBatch).filter(
        StockBatch.expiry_date <= today + timedelta(days=90),
        StockBatch.expiry_date >= today, StockBatch.quantity > 0).count()
    refills_due = db.query(RefillSchedule).filter(
        RefillSchedule.is_active == True,
        RefillSchedule.next_refill_date <= today + timedelta(days=3),
        RefillSchedule.next_refill_date >= today).count()
    pending_pos = db.query(PurchaseOrder).filter(PurchaseOrder.status.in_([POStatus.draft, POStatus.approved])).count()
    tx_today = db.query(DispensingRecord).filter(func.date(DispensingRecord.created_at) == today).count()
    rev_change = (today_sales / yesterday_sales * 100 - 100) if yesterday_sales else 0
    return {
        "today": str(today),
        "revenue": {"today_ngn": today_sales, "yesterday_ngn": yesterday_sales, "change_pct": round(rev_change, 1), "transactions": tx_today},
        "inventory": {"low_stock_items": low_stock, "expiring_soon": expiring},
        "patients": {"refills_due": refills_due},
        "procurement": {"pending_pos": pending_pos},
        "fx": {"usd_ngn": get_cached_fx_rate(), "source": "AbokiFX"},
    }

@router.get("/revenue-chart")
def revenue_chart(days: int = 7, db: Session = Depends(get_db)):
    return [
        {"date": str(date.today() - timedelta(days=i)),
         "revenue_ngn": float(db.query(func.coalesce(func.sum(DispensingRecord.total_ngn), 0))
            .filter(func.date(DispensingRecord.created_at) == date.today() - timedelta(days=i)).scalar() or 0)}
        for i in range(days - 1, -1, -1)
    ]
