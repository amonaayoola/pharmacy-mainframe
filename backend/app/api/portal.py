"""
Patient Portal Router — Phase 4
Endpoints: /api/portal
All endpoints require a valid patient JWT (Bearer token).
"""

from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.patient_auth_service import get_patient_from_token
from app.services import patient_portal_service

router = APIRouter()


# ─────────────────────────────────────────────
# REQUEST SCHEMAS
# ─────────────────────────────────────────────

class AllergyIn(BaseModel):
    allergen: str
    severity: str = "mild"   # mild | moderate | severe


class RefillRequestIn(BaseModel):
    drug_id: int
    notes: Optional[str] = None


# ─────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────

@router.get("/me")
def get_me(
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """Return the authenticated patient's profile and current medications."""
    return patient_portal_service.get_profile(patient_id, db)


@router.get("/medications")
def get_medications(
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """
    Return the medication timeline: active schedules with quantity remaining
    and predicted runout date.
    """
    return patient_portal_service.get_medications(patient_id, db)


@router.get("/health-card")
def get_health_card(
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """
    Return a structured health card (patient_id + name + allergies + current meds)
    suitable for QR code generation by the frontend.
    """
    return patient_portal_service.get_health_card(patient_id, db)


@router.post("/allergies", status_code=201)
def add_allergy(
    payload: AllergyIn,
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """Add an allergy to the patient's profile. Used at POS hard block."""
    return patient_portal_service.add_allergy(
        patient_id, payload.allergen, payload.severity, db
    )


@router.delete("/allergies/{allergy_id}", status_code=200)
def delete_allergy(
    allergy_id: int,
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """Remove an allergy. Patient can only delete their own allergy records."""
    return patient_portal_service.delete_allergy(patient_id, allergy_id, db)


@router.post("/refill-request", status_code=201)
def create_refill_request(
    payload: RefillRequestIn,
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """
    Submit a refill request. Notifies the pharmacy via WhatsApp.
    Drug must be on the patient's active medication schedule.
    """
    return patient_portal_service.create_refill_request(
        patient_id, payload.drug_id, payload.notes, db
    )


@router.get("/refill-requests")
def list_refill_requests(
    patient_id: int = Depends(get_patient_from_token),
    db: Session = Depends(get_db),
):
    """List all of the patient's refill requests and their current status."""
    return patient_portal_service.list_refill_requests(patient_id, db)
