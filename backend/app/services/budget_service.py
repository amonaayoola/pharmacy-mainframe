"""
Budget Service — Phase 3D
Monthly budget tracking, reconciliation, and spend controls.
"""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import PurchaseOrder, ProcurementLine, Drug, POStatus, ProcurementBudget
from app.models.procurement_models import BudgetTracking, MonthlyReconciliation

logger = logging.getLogger(__name__)


def check_budget_available(db: Session, category: str, amount_ngn: float) -> Dict:
    """
    Check if budget is available for a category this month.
    Returns {available: bool, remaining: float|None, ceiling: float|None}
    """
    today = date.today()
    budget = (
        db.query(ProcurementBudget)
        .filter_by(category=category, year=today.year, month=today.month)
        .first()
    )
    if not budget or float(budget.monthly_budget_ngn) == 0:
        return {"available": True, "remaining": None, "ceiling": None, "reason": "No ceiling set"}

    ceiling = float(budget.monthly_budget_ngn)
    spent = float(budget.spent_ngn)
    remaining = ceiling - spent

    if spent + amount_ngn > ceiling:
        return {
            "available": False,
            "ceiling": ceiling,
            "spent": spent,
            "remaining": remaining,
            "requested": amount_ngn,
            "reason": f"Would exceed monthly budget by ₦{(spent + amount_ngn - ceiling):,.2f}",
        }
    return {
        "available": True,
        "ceiling": ceiling,
        "spent": spent,
        "remaining": remaining - amount_ngn,
        "reason": "Within budget",
    }


def track_spending(db: Session, category: str, amount_ngn: float) -> Dict:
    """
    Record spend against a category's monthly budget and update BudgetTracking.
    """
    today = date.today()
    month_start = date(today.year, today.month, 1)

    # Update ProcurementBudget (legacy)
    budget = (
        db.query(ProcurementBudget)
        .filter_by(category=category, year=today.year, month=today.month)
        .first()
    )
    if budget:
        budget.spent_ngn = float(budget.spent_ngn) + amount_ngn

    # Update BudgetTracking (Phase 3D)
    tracking = (
        db.query(BudgetTracking)
        .filter_by(month=month_start, category=category)
        .first()
    )
    if not tracking:
        # Seed from ProcurementBudget if available
        budgeted = float(budget.monthly_budget_ngn) if budget else 0
        tracking = BudgetTracking(
            month=month_start,
            category=category,
            budgeted=budgeted,
            spent=0,
            variance=budgeted,
        )
        db.add(tracking)
        db.flush()

    tracking.spent = float(tracking.spent) + amount_ngn
    tracking.variance = float(tracking.budgeted) - float(tracking.spent)
    db.flush()
    return {"category": category, "new_spent": float(tracking.spent), "variance": float(tracking.variance)}


def get_budget_status(db: Session) -> Dict:
    """
    Return current month's full budget status across all categories.
    """
    today = date.today()
    month_start = date(today.year, today.month, 1)

    trackings = (
        db.query(BudgetTracking)
        .filter(BudgetTracking.month == month_start)
        .all()
    )

    # Also pull from ProcurementBudget for categories not in BudgetTracking yet
    budgets = (
        db.query(ProcurementBudget)
        .filter_by(year=today.year, month=today.month)
        .all()
    )

    categories = {}
    for b in budgets:
        categories[b.category] = {
            "category": b.category,
            "budgeted": float(b.monthly_budget_ngn),
            "spent": float(b.spent_ngn),
            "remaining": float(b.monthly_budget_ngn) - float(b.spent_ngn) if float(b.monthly_budget_ngn) > 0 else None,
            "utilisation_pct": round(float(b.spent_ngn) / float(b.monthly_budget_ngn) * 100, 1) if float(b.monthly_budget_ngn) > 0 else 0,
        }

    for t in trackings:
        cat = t.category
        if cat not in categories:
            categories[cat] = {
                "category": cat,
                "budgeted": float(t.budgeted),
                "spent": float(t.spent),
                "remaining": float(t.variance),
                "utilisation_pct": round(float(t.spent) / float(t.budgeted) * 100, 1) if float(t.budgeted) > 0 else 0,
            }

    rows = list(categories.values())
    return {
        "month": month_start.isoformat(),
        "categories": rows,
        "total_budgeted": sum(r["budgeted"] for r in rows),
        "total_spent": sum(r["spent"] for r in rows),
    }


def monthly_reconciliation(db: Session, reconciled_by: str, month: Optional[date] = None) -> Dict:
    """
    Reconcile ordered vs received quantities for the given month.
    Creates a MonthlyReconciliation record.
    """
    today = date.today()
    target_month = month or date(today.year, today.month, 1)

    # Fetch all received POs in the month
    from sqlalchemy import extract
    pos = (
        db.query(PurchaseOrder)
        .filter(
            PurchaseOrder.status.in_([POStatus.received, POStatus.paid]),
            extract("year", PurchaseOrder.received_at) == target_month.year,
            extract("month", PurchaseOrder.received_at) == target_month.month,
        )
        .all()
    )

    po_count = len(pos)
    total_ordered = 0.0
    total_received = 0.0
    discrepancies = 0

    for po in pos:
        for line in po.lines:
            line_ordered = float(line.total_ngn or 0)
            # Use quantity_received / quantity_ordered ratio to estimate received value
            ratio = (line.quantity_received or 0) / line.quantity_ordered if line.quantity_ordered else 1
            line_received = line_ordered * ratio
            total_ordered += line_ordered
            total_received += line_received
            if (line.quantity_received or 0) != line.quantity_ordered:
                discrepancies += 1

    variance_pct = round((total_ordered - total_received) / total_ordered * 100, 3) if total_ordered > 0 else 0

    # Upsert reconciliation record
    existing = db.query(MonthlyReconciliation).filter_by(month=target_month).first()
    if existing:
        rec = existing
    else:
        rec = MonthlyReconciliation(month=target_month)
        db.add(rec)

    rec.po_count = po_count
    rec.total_ordered = round(total_ordered, 2)
    rec.total_received = round(total_received, 2)
    rec.discrepancies = discrepancies
    rec.variance_pct = variance_pct
    rec.reconciled_by = reconciled_by
    rec.reconciled_at = datetime.utcnow()
    db.commit()

    return {
        "month": target_month.isoformat(),
        "po_count": po_count,
        "total_ordered_ngn": round(total_ordered, 2),
        "total_received_ngn": round(total_received, 2),
        "discrepancies": discrepancies,
        "variance_pct": variance_pct,
        "reconciled_by": reconciled_by,
        "reconciled_at": rec.reconciled_at.isoformat(),
    }
