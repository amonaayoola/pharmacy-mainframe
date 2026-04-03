"""
Inventory Alerts API
FastAPI endpoints for stockout alerts and inventory management.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import List, Optional
import logging

from backend.app.core.database import get_db
from backend.app.services.inventory_analytics import InventoryAnalytics
from backend.app.services.procurement_trigger import ProcurementTrigger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inventory", tags=["inventory"])


class AlertSchema:
    """Pydantic schema for alerts."""
    def __init__(self, alert_id: int, drug_id: int, drug_name: str, 
                 alert_type: str, current_stock: float, 
                 daily_velocity: float, days_until_event: float,
                 reason: str, created_at: datetime, acknowledged: bool = False):
        self.alert_id = alert_id
        self.drug_id = drug_id
        self.drug_name = drug_name
        self.alert_type = alert_type
        self.current_stock = current_stock
        self.daily_velocity = daily_velocity
        self.days_until_event = days_until_event
        self.reason = reason
        self.created_at = created_at
        self.acknowledged = acknowledged
    
    def dict(self):
        return {
            "alert_id": self.alert_id,
            "drug_id": self.drug_id,
            "drug_name": self.drug_name,
            "alert_type": self.alert_type,
            "current_stock": self.current_stock,
            "daily_velocity": self.daily_velocity,
            "days_until_event": self.days_until_event,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "acknowledged": self.acknowledged
        }


@router.get("/alerts")
def get_active_alerts(session: Session = Depends(get_db)):
    """
    Get all active (unacknowledged) inventory alerts.
    
    Returns:
        List of active alerts with stockout and margin erosion info
    """
    try:
        alerts = []
        
        # Get drugs needing reorder (stockout risk)
        reorder_drugs = InventoryAnalytics.get_drugs_needing_reorder(session)
        
        for drug in reorder_drugs:
            alert = AlertSchema(
                alert_id=hash(f"stockout_{drug['drug_id']}") % 10000,
                drug_id=drug['drug_id'],
                drug_name=drug['name'],
                alert_type="stockout_risk",
                current_stock=drug['current_stock'],
                daily_velocity=drug['daily_velocity'],
                days_until_event=drug['days_until_stockout'],
                reason=f"Stock depleting at {drug['daily_velocity']:.2f} units/day. "
                       f"Will stockout in {drug['days_until_stockout']:.1f} days.",
                created_at=datetime.utcnow()
            )
            alerts.append(alert.dict())
        
        # Check for margin erosion
        query = text("SELECT id, name FROM drugs WHERE active = true")
        drugs = session.execute(query).fetchall()
        
        for drug_id, drug_name in drugs:
            if InventoryAnalytics.check_margin_erosion(session, drug_id):
                alert = AlertSchema(
                    alert_id=hash(f"margin_{drug_id}") % 10000,
                    drug_id=drug_id,
                    drug_name=drug_name,
                    alert_type="margin_erosion",
                    current_stock=InventoryAnalytics.calculate_current_stock(session, drug_id),
                    daily_velocity=InventoryAnalytics.calculate_daily_velocity(session, drug_id),
                    days_until_event=0,
                    reason="Cost price > retail price. Margin eroded due to FX shock.",
                    created_at=datetime.utcnow()
                )
                alerts.append(alert.dict())
        
        logger.info(f"Generated {len(alerts)} alerts")
        return {"alerts": alerts, "count": len(alerts)}
    
    except Exception as e:
        logger.error(f"Error generating alerts: {str(e)}")
        raise HTTPException(status_code=500, detail="Error generating alerts")


@router.post("/acknowledge/{alert_id}")
def acknowledge_alert(alert_id: int, session: Session = Depends(get_db)):
    """
    Mark an alert as acknowledged by pharmacist.
    
    Args:
        alert_id: Alert ID to acknowledge
    
    Returns:
        Confirmation with timestamp
    """
    try:
        # For now, log acknowledgment (would persist to alerts table in production)
        logger.info(f"Alert {alert_id} acknowledged by pharmacist at {datetime.utcnow()}")
        
        return {
            "alert_id": alert_id,
            "acknowledged_at": datetime.utcnow().isoformat(),
            "status": "acknowledged"
        }
    except Exception as e:
        logger.error(f"Error acknowledging alert: {str(e)}")
        raise HTTPException(status_code=500, detail="Error acknowledging alert")


@router.get("/history")
def get_alert_history(
    days: int = Query(30, ge=1, le=90),
    alert_type: Optional[str] = None,
    session: Session = Depends(get_db)
):
    """
    Get alert history for past N days.
    
    Args:
        days: Number of days to look back (default 30, max 90)
        alert_type: Filter by 'stockout_risk' or 'margin_erosion' (optional)
    
    Returns:
        List of alerts from specified period
    """
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        # In production, this would query an alerts_history table
        # For now, return current state with simulated history
        history = []
        
        reorder_drugs = InventoryAnalytics.get_drugs_needing_reorder(session)
        for drug in reorder_drugs:
            if alert_type is None or alert_type == "stockout_risk":
                history.append({
                    "alert_id": hash(f"stockout_{drug['drug_id']}") % 10000,
                    "drug_id": drug['drug_id'],
                    "drug_name": drug['name'],
                    "alert_type": "stockout_risk",
                    "days_until_stockout": drug['days_until_stockout'],
                    "created_at": datetime.utcnow().isoformat()
                })
        
        logger.info(f"Returned {len(history)} alerts from past {days} days")
        return {
            "period_days": days,
            "alert_type_filter": alert_type,
            "count": len(history),
            "alerts": history
        }
    
    except Exception as e:
        logger.error(f"Error fetching alert history: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching history")


@router.post("/trigger-procurement")
def trigger_procurement_from_alerts(session: Session = Depends(get_db)):
    """
    Trigger procurement process for all drugs with active stockout alerts.
    Creates draft PurchaseOrders for pharmacist review.
    
    Returns:
        List of draft POs created
    """
    try:
        reorder_drugs = InventoryAnalytics.get_drugs_needing_reorder(session)
        draft_pos = []
        
        for drug in reorder_drugs:
            try:
                po = ProcurementTrigger.create_draft_purchase_order(
                    session,
                    drug['drug_id'],
                    reason=f"Stockout alert: {drug['days_until_stockout']:.1f} days remaining"
                )
                draft_pos.append(po)
            except Exception as e:
                logger.error(f"Failed to create PO for drug {drug['drug_id']}: {str(e)}")
        
        logger.info(f"Created {len(draft_pos)} draft POs")
        return {
            "status": "success",
            "draft_pos_created": len(draft_pos),
            "purchase_orders": draft_pos
        }
    
    except Exception as e:
        logger.error(f"Error triggering procurement: {str(e)}")
        raise HTTPException(status_code=500, detail="Error creating draft POs")
