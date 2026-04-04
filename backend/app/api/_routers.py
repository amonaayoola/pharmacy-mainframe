"""
API Routers — Pharmacy Intelligence Mainframe
This file is deprecated. Use individual router files in this directory instead:
  - pricing.py
  - nafdac.py
  - whatsapp.py
  - dashboard.py
  - etc.
"""

# This file is kept for backward compatibility but contains no code.
# All routers are imported directly from their individual modules in main.py


"""dashboard.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from datetime import date, timedelta, datetime
from app.core.database import get_db
from app.models.models import (
    DispensingRecord, StockBatch, Patient, RefillSchedule,
    PurchaseOrder, POStatus, StockStatus
)
from app.services.fx_service import get_cached_fx_rate

dashboard_router = APIRouter()

@dashboard_router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Today's revenue
    today_sales = db.query(
        func.coalesce(func.sum(DispensingRecord.total_ngn), 0)
    ).filter(
        func.date(DispensingRecord.created_at) == today,
        DispensingRecord.is_refund == False,
    ).scalar() or 0

    yesterday_sales = db.query(
        func.coalesce(func.sum(DispensingRecord.total_ngn), 0)
    ).filter(
        func.date(DispensingRecord.created_at) == yesterday,
        DispensingRecord.is_refund == False,
    ).scalar() or 1

    # Low stock (expired items or items flagged)
    low_stock_count = db.query(StockBatch).filter(
        StockBatch.status.in_([StockStatus.low, StockStatus.critical, StockStatus.out])
    ).count()

    # Expiring soon
    expiry_cutoff = today + timedelta(days=90)
    expiring_count = db.query(StockBatch).filter(
        StockBatch.expiry_date <= expiry_cutoff,
        StockBatch.expiry_date >= today,
        StockBatch.quantity > 0,
    ).count()

    # Refills due
    refill_cutoff = today + timedelta(days=3)
    refills_due = db.query(RefillSchedule).filter(
        RefillSchedule.is_active == True,
        RefillSchedule.next_refill_date <= refill_cutoff,
        RefillSchedule.next_refill_date >= today,
    ).count()

    # Pending POs
    pending_pos = db.query(PurchaseOrder).filter(
        PurchaseOrder.status.in_([POStatus.draft, POStatus.approved])
    ).count()

    # Transaction count today
    tx_today = db.query(DispensingRecord).filter(
        func.date(DispensingRecord.created_at) == today
    ).count()

    rev_change = float(today_sales) / float(yesterday_sales) * 100 - 100 if yesterday_sales else 0

    return {
        "today": str(today),
        "revenue": {
            "today_ngn": float(today_sales),
            "yesterday_ngn": float(yesterday_sales),
            "change_pct": round(rev_change, 1),
            "transactions": tx_today,
        },
        "inventory": {
            "low_stock_items": low_stock_count,
            "expiring_soon": expiring_count,
        },
        "patients": {
            "refills_due": refills_due,
        },
        "procurement": {
            "pending_pos": pending_pos,
        },
        "fx": {
            "usd_ngn": get_cached_fx_rate(),
            "source": "AbokiFX",
        },
    }

@dashboard_router.get("/revenue-chart")
def revenue_chart(days: int = 7, db: Session = Depends(get_db)):
    """Last N days of daily revenue for the chart."""
    result = []
    for i in range(days - 1, -1, -1):
        d = date.today() - timedelta(days=i)
        revenue = db.query(
            func.coalesce(func.sum(DispensingRecord.total_ngn), 0)
        ).filter(func.date(DispensingRecord.created_at) == d).scalar() or 0
        result.append({"date": str(d), "revenue_ngn": float(revenue)})
    return result

router = dashboard_router
