"""
Procurement Trigger Service
Auto-creates draft PurchaseOrders when inventory triggers alert conditions.
"""

import logging
from datetime import datetime
from typing import Dict, Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
import json

logger = logging.getLogger(__name__)


class ProcurementTrigger:
    """Generate draft purchase orders from inventory alerts."""
    
    @staticmethod
    def create_draft_purchase_order(
        session: Session,
        drug_id: int,
        quantity: Optional[int] = None,
        reason: str = "Automated inventory alert"
    ) -> Dict:
        """
        Create a draft PurchaseOrder (not submitted, requires pharmacist approval).
        
        Args:
            session: Database session
            drug_id: Drug to procure
            quantity: Quantity to order (if None, use calculated amount)
            reason: Reason for PO (logged for pharmacist context)
        
        Returns:
            Draft PO details
        """
        try:
            # Get drug info
            drug_query = text("""
                SELECT id, name, cost_price, supplier_id 
                FROM drugs 
                WHERE id = :drug_id
            """)
            drug = session.execute(drug_query, {"drug_id": drug_id}).first()
            
            if not drug:
                raise ValueError(f"Drug {drug_id} not found")
            
            drug_id, drug_name, cost_price, supplier_id = drug
            
            # Calculate order quantity if not provided
            if quantity is None:
                quantity = ProcurementTrigger._calculate_order_quantity(session, drug_id)
            
            # Create draft PO
            po_query = text("""
                INSERT INTO purchase_orders (
                    drug_id, supplier_id, quantity_ordered, 
                    status, created_at, reasoning_notes
                )
                VALUES (
                    :drug_id, :supplier_id, :quantity,
                    'draft', :created_at, :reasoning_notes
                )
                RETURNING id, created_at
            """)
            
            reasoning = f"Automated: {reason}. Order: {quantity} units @ {cost_price}/unit = {quantity * cost_price} NGN total"
            
            result = session.execute(po_query, {
                "drug_id": drug_id,
                "supplier_id": supplier_id,
                "quantity": quantity,
                "created_at": datetime.utcnow(),
                "reasoning_notes": reasoning
            }).first()
            
            session.commit()
            po_id, created_at = result
            
            logger.info(
                f"Created draft PO #{po_id}: {drug_name}, "
                f"{quantity} units, {reasoning}"
            )
            
            return {
                "po_id": po_id,
                "drug_id": drug_id,
                "drug_name": drug_name,
                "quantity": quantity,
                "unit_cost": float(cost_price),
                "total_cost": float(quantity * cost_price),
                "status": "draft",
                "reasoning": reasoning,
                "created_at": created_at.isoformat(),
                "requires_approval": True,
                "approval_url": f"/procurement/approve/{po_id}"
            }
        
        except Exception as e:
            logger.error(f"Failed to create draft PO for drug {drug_id}: {str(e)}")
            session.rollback()
            raise
    
    @staticmethod
    def _calculate_order_quantity(session: Session, drug_id: int) -> int:
        """
        Calculate recommended order quantity based on velocity and lead time.
        
        Formula: (daily_velocity * lead_time_days) + safety_stock
        Assumptions: 7-day lead time, 14-day safety stock
        """
        try:
            # Get current stock and velocity
            velocity_query = text("""
                SELECT 
                    COALESCE(SUM(quantity_change), 0) as current_stock
                FROM inventory_movements
                WHERE drug_id = :drug_id
            """)
            
            current_stock = session.execute(
                velocity_query, 
                {"drug_id": drug_id}
            ).scalar() or 0
            
            # Get daily velocity from last 30 days
            velocity_query = text("""
                SELECT 
                    COALESCE(SUM(quantity) / 30, 0) as daily_velocity
                FROM inventory_movements
                WHERE drug_id = :drug_id
                    AND movement_type = 'dispensed'
                    AND created_at >= NOW() - INTERVAL '30 days'
            """)
            
            daily_velocity = session.execute(
                velocity_query,
                {"drug_id": drug_id}
            ).scalar() or 1  # Default to 1 unit/day if no history
            
            # Calculate order: (lead_time * velocity) + safety_stock
            lead_time_days = 7
            safety_stock_days = 14
            order_quantity = int(
                (daily_velocity * (lead_time_days + safety_stock_days)) * 1.1  # 10% buffer
            )
            
            logger.info(
                f"Drug {drug_id}: velocity={daily_velocity:.2f}/day, "
                f"current_stock={current_stock}, calculated_order={order_quantity}"
            )
            
            return max(order_quantity, 10)  # Minimum 10 units
        
        except Exception as e:
            logger.error(f"Error calculating order quantity for drug {drug_id}: {str(e)}")
            return 50  # Safe default
    
    @staticmethod
    def approve_draft_po(session: Session, po_id: int) -> Dict:
        """
        Approve a draft PO for submission to supplier.
        
        Args:
            session: Database session
            po_id: Draft PO ID to approve
        
        Returns:
            Updated PO info
        """
        try:
            update_query = text("""
                UPDATE purchase_orders
                SET status = 'submitted', submitted_at = :submitted_at
                WHERE id = :po_id AND status = 'draft'
                RETURNING id, drug_id, quantity_ordered, status
            """)
            
            result = session.execute(update_query, {
                "po_id": po_id,
                "submitted_at": datetime.utcnow()
            }).first()
            
            session.commit()
            
            if not result:
                raise ValueError(f"PO {po_id} not found or not in draft status")
            
            po_id, drug_id, quantity, status = result
            logger.info(f"Approved draft PO #{po_id}, submitted to supplier")
            
            return {
                "po_id": po_id,
                "drug_id": drug_id,
                "quantity": quantity,
                "status": status,
                "submitted_at": datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Error approving PO {po_id}: {str(e)}")
            session.rollback()
            raise
    
    @staticmethod
    def reject_draft_po(session: Session, po_id: int, reason: str = "") -> Dict:
        """
        Reject a draft PO (pharmacist decision).
        
        Args:
            session: Database session
            po_id: Draft PO ID to reject
            reason: Reason for rejection
        
        Returns:
            Rejection confirmation
        """
        try:
            update_query = text("""
                UPDATE purchase_orders
                SET status = 'rejected', reasoning_notes = :notes
                WHERE id = :po_id AND status = 'draft'
                RETURNING id, drug_id
            """)
            
            result = session.execute(update_query, {
                "po_id": po_id,
                "notes": f"Rejected: {reason}" if reason else "Rejected"
            }).first()
            
            session.commit()
            
            if not result:
                raise ValueError(f"PO {po_id} not found or not in draft status")
            
            po_id, drug_id = result
            logger.info(f"Rejected draft PO #{po_id} for drug {drug_id}")
            
            return {
                "po_id": po_id,
                "status": "rejected",
                "reason": reason,
                "rejected_at": datetime.utcnow().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Error rejecting PO {po_id}: {str(e)}")
            session.rollback()
            raise
