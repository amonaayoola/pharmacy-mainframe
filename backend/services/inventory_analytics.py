"""
Inventory Analytics Service
Calculates daily stock velocity, predicts stockouts, and triggers reorder alerts.
"""

from datetime import datetime, timedelta
from typing import Optional, List, Dict
import logging
from sqlalchemy import text, func
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class InventoryAnalytics:
    """Calculate daily stock velocity and predict stockouts."""
    
    STOCKOUT_THRESHOLD_DAYS = 7  # Alert if < 7 days of stock remaining
    
    @staticmethod
    def calculate_daily_velocity(session: Session, drug_id: int, days: int = 30) -> float:
        """
        Calculate average daily sales velocity for a drug.
        
        Args:
            session: Database session
            drug_id: Drug ID
            days: Number of days to look back (default 30)
        
        Returns:
            Average units sold per day
        """
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        query = text("""
            SELECT 
                COALESCE(SUM(quantity), 0) as total_units
            FROM inventory_movements
            WHERE drug_id = :drug_id
                AND movement_type = 'dispensed'
                AND created_at >= :cutoff_date
        """)
        
        result = session.execute(
            query,
            {"drug_id": drug_id, "cutoff_date": cutoff_date}
        ).first()
        
        total_units = result[0] if result else 0
        daily_velocity = total_units / days if days > 0 else 0
        
        logger.info(f"Drug {drug_id}: {total_units} units in {days} days = {daily_velocity:.2f} units/day")
        return daily_velocity
    
    @staticmethod
    def calculate_current_stock(session: Session, drug_id: int) -> float:
        """Get current stock level for a drug."""
        query = text("""
            SELECT COALESCE(SUM(quantity_change), 0)
            FROM inventory_movements
            WHERE drug_id = :drug_id
        """)
        
        result = session.execute(query, {"drug_id": drug_id}).first()
        return float(result[0]) if result else 0
    
    @staticmethod
    def predict_stockout_days(session: Session, drug_id: int) -> Optional[float]:
        """
        Predict days until stockout based on current velocity.
        
        Returns:
            Days until stockout, or None if velocity is 0
        """
        velocity = InventoryAnalytics.calculate_daily_velocity(session, drug_id)
        if velocity <= 0:
            return None
        
        current_stock = InventoryAnalytics.calculate_current_stock(session, drug_id)
        days_until_stockout = current_stock / velocity
        
        return days_until_stockout
    
    @staticmethod
    def check_margin_erosion(session: Session, drug_id: int) -> bool:
        """
        Check if drug cost > retail price (margin erosion after FX shock).
        
        Returns:
            True if cost > retail price, False otherwise
        """
        query = text("""
            SELECT 
                d.cost_price,
                d.retail_price
            FROM drugs d
            WHERE d.id = :drug_id
        """)
        
        result = session.execute(query, {"drug_id": drug_id}).first()
        if not result:
            return False
        
        cost_price, retail_price = result
        
        # Margin erosion if cost exceeds retail
        is_eroded = cost_price > retail_price
        
        if is_eroded:
            logger.warning(
                f"Drug {drug_id}: Margin erosion detected! "
                f"Cost: {cost_price}, Retail: {retail_price}"
            )
        
        return is_eroded
    
    @staticmethod
    def get_drugs_needing_reorder(session: Session) -> List[Dict]:
        """
        Get all drugs that need reordering based on stockout prediction.
        
        Returns:
            List of dicts with drug_id, name, current_stock, daily_velocity, days_until_stockout
        """
        query = text("""
            SELECT 
                d.id,
                d.name,
                COALESCE(SUM(im.quantity_change), 0) as current_stock
            FROM drugs d
            LEFT JOIN inventory_movements im ON d.id = im.drug_id
            GROUP BY d.id, d.name
            HAVING current_stock > 0
        """)
        
        results = session.execute(query).fetchall()
        reorder_list = []
        
        for drug_id, name, current_stock in results:
            velocity = InventoryAnalytics.calculate_daily_velocity(session, drug_id)
            
            if velocity > 0:
                days_until_stockout = current_stock / velocity
                
                if days_until_stockout < InventoryAnalytics.STOCKOUT_THRESHOLD_DAYS:
                    reorder_list.append({
                        "drug_id": drug_id,
                        "name": name,
                        "current_stock": current_stock,
                        "daily_velocity": velocity,
                        "days_until_stockout": days_until_stockout
                    })
        
        return reorder_list
