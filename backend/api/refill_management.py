"""
backend/api/refill_management.py
Phase 1D – Refill Management API

Endpoints:
    GET  /patients/due-for-refill
    GET  /patients/{patient_id}/refill-history
    POST /patients/{patient_id}/send-refill-reminder
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.services.refill_analytics import AdherenceProfile, RefillAnalyticsEngine
from backend.services.refill_outreach import RefillOutreachService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["Refill Management"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class RefillPatientOut(BaseModel):
    patient_id: int
    drug_id: int
    drug_name: str
    avg_daily_consumption: float
    adherence_rate: float
    last_dispense_date: Optional[date]
    days_supply_remaining: float
    predicted_stockout_date: Optional[date]
    days_until_stockout: Optional[int]
    refill_due_date: Optional[date]
    urgency: str = Field(
        description="'critical' ≤2 days | 'high' ≤4 days | 'medium' ≤7 days"
    )

    class Config:
        from_attributes = True

    @staticmethod
    def from_profile(p: AdherenceProfile) -> "RefillPatientOut":
        days = p.days_until_stockout
        if days is None:
            urgency = "unknown"
        elif days <= 2:
            urgency = "critical"
        elif days <= 4:
            urgency = "high"
        else:
            urgency = "medium"
        return RefillPatientOut(
            patient_id=p.patient_id,
            drug_id=p.drug_id,
            drug_name=p.drug_name,
            avg_daily_consumption=p.avg_daily_consumption,
            adherence_rate=p.adherence_rate,
            last_dispense_date=p.last_dispense_date,
            days_supply_remaining=p.days_supply_remaining,
            predicted_stockout_date=p.predicted_stockout_date,
            days_until_stockout=days,
            refill_due_date=p.refill_due_date,
            urgency=urgency,
        )


class RefillHistoryEntry(BaseModel):
    dispense_date: date
    drug_id: int
    drug_name: str
    quantity_dispensed: int
    days_supply: Optional[float]
    adherence_rate_at_fill: Optional[float]


class RefillHistoryOut(BaseModel):
    patient_id: int
    window_days: int
    entries: List[RefillHistoryEntry]
    overall_adherence: float


class ReminderRequest(BaseModel):
    channel: str = Field(
        default="whatsapp",
        description="Delivery channel: 'whatsapp' | 'sms'",
    )
    override_message: Optional[str] = Field(
        default=None,
        description="Custom message body; leave blank for auto-generated text.",
    )


class ReminderResponse(BaseModel):
    success: bool
    message_id: Optional[str]
    channel: str
    queued_at: str


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_analytics(db: Session = Depends(get_db)) -> RefillAnalyticsEngine:
    return RefillAnalyticsEngine(db)


def get_outreach(db: Session = Depends(get_db)) -> RefillOutreachService:
    return RefillOutreachService(db)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/due-for-refill",
    response_model=List[RefillPatientOut],
    summary="List all patients needing a refill within the next 7 days",
)
def list_patients_due_for_refill(
    urgency_filter: Optional[str] = Query(
        default=None,
        description="Filter by urgency level: critical | high | medium",
    ),
    analytics: RefillAnalyticsEngine = Depends(get_analytics),
):
    """
    Returns every active patient–drug combination predicted to run out within
    the configured risk window (default: 7 days), sorted by urgency.

    Urgency tiers:
    - **critical** – 0–2 days remaining
    - **high**     – 3–4 days remaining
    - **medium**   – 5–7 days remaining
    """
    try:
        at_risk = analytics.get_at_risk_patients()
    except Exception as exc:
        logger.exception("Refill analytics failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not compute refill predictions: {exc}",
        ) from exc

    results = [RefillPatientOut.from_profile(p) for p in at_risk]

    # optional urgency filter
    if urgency_filter:
        allowed = {"critical", "high", "medium", "unknown"}
        if urgency_filter not in allowed:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"urgency_filter must be one of {sorted(allowed)}",
            )
        results = [r for r in results if r.urgency == urgency_filter]

    # sort: critical first, then by days_until_stockout asc
    order = {"critical": 0, "high": 1, "medium": 2, "unknown": 3}
    results.sort(
        key=lambda r: (
            order.get(r.urgency, 9),
            r.days_until_stockout if r.days_until_stockout is not None else 999,
        )
    )
    return results


@router.get(
    "/{patient_id}/refill-history",
    response_model=RefillHistoryOut,
    summary="90-day refill timeline for a specific patient",
)
def get_refill_history(
    patient_id: int,
    window_days: int = Query(default=90, ge=7, le=365),
    analytics: RefillAnalyticsEngine = Depends(get_analytics),
    db: Session = Depends(get_db),
):
    """
    Returns a chronological list of all dispenses for the patient over the
    requested window, together with computed adherence metrics.
    """
    from sqlalchemy import text

    sql = text(
        """
        SELECT
            dr.drug_id,
            d.name          AS drug_name,
            dr.quantity_dispensed,
            dr.dispensed_at::date AS dispense_date
        FROM dispensing_records dr
        JOIN drugs d ON d.id = dr.drug_id
        WHERE dr.patient_id     = :patient_id
          AND dr.dispensed_at  >= CURRENT_DATE - CAST(:window_days AS INT) * INTERVAL '1 day'
        ORDER BY dr.dispensed_at
        """
    )
    rows = db.execute(sql, {"patient_id": patient_id, "window_days": window_days}).fetchall()

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No dispensing history found for patient {patient_id}",
        )

    # Build entries with per-fill days-supply estimation
    profiles = analytics.get_patient_profiles(patient_id)
    avg_daily_by_drug: dict[int, float] = {
        p.drug_id: p.avg_daily_consumption for p in profiles
    }

    entries: List[RefillHistoryEntry] = []
    for row in rows:
        avg = avg_daily_by_drug.get(row.drug_id)
        days_supply = (
            round(row.quantity_dispensed / avg, 1) if avg and avg > 0 else None
        )
        entries.append(
            RefillHistoryEntry(
                dispense_date=row.dispense_date,
                drug_id=row.drug_id,
                drug_name=row.drug_name,
                quantity_dispensed=row.quantity_dispensed,
                days_supply=days_supply,
                adherence_rate_at_fill=None,  # computed in bulk summary below
            )
        )

    overall_adherence = (
        round(
            sum(p.adherence_rate for p in profiles) / len(profiles), 3
        )
        if profiles
        else 0.0
    )

    return RefillHistoryOut(
        patient_id=patient_id,
        window_days=window_days,
        entries=entries,
        overall_adherence=overall_adherence,
    )


@router.post(
    "/{patient_id}/send-refill-reminder",
    response_model=ReminderResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a WhatsApp/SMS refill reminder for a patient",
)
def send_refill_reminder(
    patient_id: int,
    body: ReminderRequest,
    analytics: RefillAnalyticsEngine = Depends(get_analytics),
    outreach: RefillOutreachService = Depends(get_outreach),
):
    """
    Generates (or accepts a custom) refill reminder and queues it for
    immediate delivery via the specified channel.

    Delivery status can be tracked via the outreach log table.
    """
    profiles = analytics.get_patient_profiles(patient_id)
    if not profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active medication profiles found for patient {patient_id}",
        )

    at_risk = [p for p in profiles if p.at_risk]
    if not at_risk:
        # Still allow manual override, but surface the info
        logger.info(
            "Manual reminder requested for patient %s — not currently at risk",
            patient_id,
        )

    target_profiles = at_risk or profiles  # send for at-risk drugs, fallback to all

    try:
        result = outreach.queue_reminder(
            patient_id=patient_id,
            profiles=target_profiles,
            channel=body.channel,
            custom_message=body.override_message,
        )
    except Exception as exc:
        logger.exception("Failed to queue reminder for patient %s", patient_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Outreach service error: {exc}",
        ) from exc

    return ReminderResponse(
        success=result["success"],
        message_id=result.get("message_id"),
        channel=body.channel,
        queued_at=result["queued_at"],
    )
