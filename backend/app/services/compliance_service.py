"""
Compliance Service — Phase 3D
NAFDAC status validation, expiry checks, seasonal adjustment, and audit trail.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.models import Drug, StockBatch, NAFDACStatus, PurchaseOrder, ProcurementLine, POStatus
from app.models.procurement_models import ComplianceFlag, SeasonalForecast, POTracking

logger = logging.getLogger(__name__)


def check_nafdac_status(db: Session, drug_id: Optional[int] = None) -> Dict:
    """
    Check NAFDAC registration status of drugs.
    Returns list of drugs with missing or flagged NAFDAC status.
    """
    q = db.query(Drug).filter(Drug.is_active == True)
    if drug_id:
        q = q.filter(Drug.id == drug_id)
    drugs = q.all()

    issues = []
    for drug in drugs:
        if not drug.nafdac_reg_no:
            issues.append({
                "drug_id": drug.id,
                "generic_name": drug.generic_name,
                "brand_name": drug.brand_name,
                "issue": "no_nafdac_reg_no",
                "severity": "block",
            })
        else:
            # Check if any batch is flagged or counterfeit
            flagged_batches = (
                db.query(StockBatch)
                .filter(
                    StockBatch.drug_id == drug.id,
                    StockBatch.nafdac_status.in_([NAFDACStatus.flagged, NAFDACStatus.counterfeit]),
                )
                .all()
            )
            for batch in flagged_batches:
                issues.append({
                    "drug_id": drug.id,
                    "generic_name": drug.generic_name,
                    "batch_no": batch.batch_no,
                    "issue": f"batch_{batch.nafdac_status.value}",
                    "severity": "block",
                })

    if drug_id and not issues:
        drug = db.query(Drug).filter(Drug.id == drug_id).first()
        return {
            "drug_id": drug_id,
            "compliant": True,
            "nafdac_reg_no": drug.nafdac_reg_no if drug else None,
            "issues": [],
        }

    return {
        "checked": len(drugs),
        "issues_found": len(issues),
        "issues": issues,
    }


def validate_expiry_dates(db: Session, warn_days: int = 90) -> Dict:
    """
    Validate all stock batches for expiry issues.
    Returns batches that are expired or expiring within warn_days.
    """
    today = date.today()
    warn_threshold = today + timedelta(days=warn_days)

    expired = (
        db.query(StockBatch)
        .filter(StockBatch.expiry_date < today, StockBatch.quantity > 0)
        .all()
    )
    expiring = (
        db.query(StockBatch)
        .filter(
            StockBatch.expiry_date >= today,
            StockBatch.expiry_date <= warn_threshold,
            StockBatch.quantity > 0,
        )
        .all()
    )

    return {
        "checked_at": today.isoformat(),
        "warn_threshold_days": warn_days,
        "expired_batches": [
            {
                "batch_id": b.id,
                "batch_no": b.batch_no,
                "drug_id": b.drug_id,
                "drug_name": b.drug.generic_name if b.drug else None,
                "quantity": b.quantity,
                "expiry_date": b.expiry_date.isoformat(),
                "days_overdue": (today - b.expiry_date).days,
            }
            for b in expired
        ],
        "expiring_soon": [
            {
                "batch_id": b.id,
                "batch_no": b.batch_no,
                "drug_id": b.drug_id,
                "drug_name": b.drug.generic_name if b.drug else None,
                "quantity": b.quantity,
                "expiry_date": b.expiry_date.isoformat(),
                "days_remaining": (b.expiry_date - today).days,
            }
            for b in expiring
        ],
        "expired_count": len(expired),
        "expiring_count": len(expiring),
    }


def seasonal_adjustment(db: Session, drug_id: int, base_quantity: int) -> Dict:
    """
    Apply seasonal demand multiplier to a base order quantity.
    """
    today = date.today()
    forecast = (
        db.query(SeasonalForecast)
        .filter_by(drug_id=drug_id, month=today.month)
        .first()
    )
    multiplier = float(forecast.demand_multiplier) if forecast else 1.0
    adjusted_qty = max(int(base_quantity * multiplier), 1)

    return {
        "drug_id": drug_id,
        "base_quantity": base_quantity,
        "month": today.month,
        "multiplier": multiplier,
        "reason": forecast.reason if forecast else "No seasonal data",
        "adjusted_quantity": adjusted_qty,
    }


def get_compliance_check(db: Session) -> Dict:
    """
    Full compliance snapshot: NAFDAC issues + expiry issues + active flags.
    """
    today = datetime.utcnow()

    nafdac = check_nafdac_status(db)
    expiry = validate_expiry_dates(db)

    # Active compliance flags
    active_flags = (
        db.query(ComplianceFlag)
        .filter(
            (ComplianceFlag.expires_at == None) | (ComplianceFlag.expires_at > today)
        )
        .order_by(ComplianceFlag.created_at.desc())
        .all()
    )

    flags_out = [
        {
            "id": f.id,
            "vendor_id": f.vendor_id,
            "drug_id": f.drug_id,
            "flag_type": f.flag_type,
            "reason": f.reason,
            "severity": f.severity,
            "expires_at": f.expires_at.isoformat() if f.expires_at else None,
            "created_at": f.created_at.isoformat(),
        }
        for f in active_flags
    ]

    blocked = sum(1 for f in active_flags if f.severity == "block")
    warnings = sum(1 for f in active_flags if f.severity == "warning")

    return {
        "checked_at": today.isoformat(),
        "nafdac_issues": nafdac.get("issues_found", 0),
        "expired_batches": expiry["expired_count"],
        "expiring_batches": expiry["expiring_count"],
        "active_compliance_flags": len(active_flags),
        "blocked_flags": blocked,
        "warning_flags": warnings,
        "flags": flags_out,
        "nafdac_detail": nafdac.get("issues", []),
        "expiry_detail": {
            "expired": expiry["expired_batches"],
            "expiring_soon": expiry["expiring_soon"],
        },
    }


def add_compliance_flag(
    db: Session,
    flag_type: str,
    reason: str,
    severity: str = "warning",
    vendor_id: Optional[int] = None,
    drug_id: Optional[int] = None,
    expires_at: Optional[datetime] = None,
) -> Dict:
    """Create a new compliance flag."""
    valid_types = {"blacklisted_vendor", "expired_batch", "temp_control_issue"}
    valid_severities = {"warning", "block"}
    if flag_type not in valid_types:
        raise ValueError(f"flag_type must be one of {valid_types}")
    if severity not in valid_severities:
        raise ValueError(f"severity must be one of {valid_severities}")

    flag = ComplianceFlag(
        vendor_id=vendor_id,
        drug_id=drug_id,
        flag_type=flag_type,
        reason=reason,
        severity=severity,
        expires_at=expires_at,
    )
    db.add(flag)
    db.commit()
    return {
        "id": flag.id,
        "vendor_id": vendor_id,
        "drug_id": drug_id,
        "flag_type": flag_type,
        "severity": severity,
        "reason": reason,
    }


def get_audit_trail(db: Session, limit: int = 100) -> List[Dict]:
    """
    Return PO tracking events as an audit trail, most recent first.
    """
    events = (
        db.query(POTracking)
        .order_by(POTracking.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "po_id": e.po_id,
            "event": e.event,
            "timestamp": e.timestamp.isoformat(),
            "notes": e.notes,
        }
        for e in events
    ]
