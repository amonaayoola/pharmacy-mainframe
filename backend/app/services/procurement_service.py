from sqlalchemy.orm import Session
from typing import Dict, Optional
from datetime import datetime
from app.models.models import PurchaseOrder, ProcurementLine, POStatus, ProcurementBudget, ProcurementAudit

def get_budget_summary(db: Session, year: Optional[int], month: Optional[int]):
    # Mock implementation for brevity, assume actual logic exists
    return {"status": "ok", "budget": "summary_data"}

def upsert_budget_amount(db: Session, category: str, amount: float, month: int, year: int):
    # Mock implementation
    return {"status": "ok"}

def approve_po(
    db: Session,
    po_id: int,
    approved_by: str,
    budget_override: bool = False,
    override_reason: Optional[str] = None,
    current_user = None,
) -> Dict:
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise ValueError("PO not found")
    
    # Simplified logic for demo
    budget_checks = {"general": po.total_usd}
    
    # Deduct budget
    for cat, amount in budget_checks.items():
        # In real system: _deduct_budget(db, cat, amount)
        pass

    # Audit the override if it happened
    if budget_override and current_user:
        audit = ProcurementAudit(
            user_id=current_user.id,
            action="BUDGET_OVERRIDE",
            details=f"PO #{po_id} override: {override_reason or 'No reason provided'}",
            timestamp=datetime.utcnow()
        )
        db.add(audit)

    po.status = POStatus.approved
    db.commit()
    return {"status": "approved", "po_id": po_id}
