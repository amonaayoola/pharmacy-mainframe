"""
Patient Portal Service — Phase 4
Business logic for all authenticated patient-facing endpoints.

All functions accept patient_id from the verified JWT — never from request body.
"""

import logging
from datetime import date, timedelta
from typing import Optional
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Patient, Drug, RefillSchedule, StockBatch
from app.models.portal_models import PatientAllergy, RefillRequest
from app.services.whatsapp_service import whatsapp_service

import asyncio
import concurrent.futures

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _send_whatsapp(phone: str, message: str) -> None:
    """Fire-and-forget WhatsApp notification, tolerates event-loop context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, whatsapp_service.send_message(phone, message)).result()
        else:
            loop.run_until_complete(whatsapp_service.send_message(phone, message))
    except Exception as e:
        logger.warning(f"WhatsApp notification failed: {e}")


def _get_patient_or_404(patient_id: int, db: Session) -> Patient:
    patient = db.query(Patient).filter(Patient.id == patient_id, Patient.is_active == True).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found.")
    return patient


def _stock_for_drug(drug_id: int, db: Session) -> int:
    """Sum all available stock batches for a drug."""
    batches = db.query(StockBatch).filter(
        StockBatch.drug_id == drug_id,
        StockBatch.quantity > 0,
    ).all()
    return sum(b.quantity for b in batches)


# ─────────────────────────────────────────────
# /me — PATIENT PROFILE + CURRENT MEDS
# ─────────────────────────────────────────────

def get_profile(patient_id: int, db: Session) -> dict:
    patient = _get_patient_or_404(patient_id, db)

    active_schedules = (
        db.query(RefillSchedule)
        .filter(RefillSchedule.patient_id == patient_id, RefillSchedule.is_active == True)
        .all()
    )

    current_meds = []
    for sched in active_schedules:
        drug = db.query(Drug).filter(Drug.id == sched.drug_id).first()
        if drug:
            current_meds.append({
                "drug_id":         drug.id,
                "generic_name":    drug.generic_name,
                "brand_name":      drug.brand_name,
                "strength":        drug.strength,
                "cycle_days":      sched.cycle_days,
                "standard_qty":    sched.standard_qty,
                "last_refill_date": sched.last_refill_date.isoformat() if sched.last_refill_date else None,
                "next_refill_date": sched.next_refill_date.isoformat() if sched.next_refill_date else None,
            })

    return {
        "patient_id":      patient.id,
        "full_name":       patient.full_name,
        "phone_number":    patient.phone_number,
        "date_of_birth":   patient.date_of_birth.isoformat() if patient.date_of_birth else None,
        "gender":          patient.gender,
        "address":         patient.address,
        "condition_tags":  patient.condition_tags or [],
        "whatsapp_opted_in": patient.whatsapp_opted_in,
        "current_medications": current_meds,
    }


# ─────────────────────────────────────────────
# /medications — MEDICATION TIMELINE
# ─────────────────────────────────────────────

def get_medications(patient_id: int, db: Session) -> list:
    _get_patient_or_404(patient_id, db)

    active_schedules = (
        db.query(RefillSchedule)
        .filter(RefillSchedule.patient_id == patient_id, RefillSchedule.is_active == True)
        .all()
    )

    result = []
    today = date.today()

    for sched in active_schedules:
        drug = db.query(Drug).filter(Drug.id == sched.drug_id).first()
        if not drug:
            continue

        qty_remaining  = _stock_for_drug(drug.id, db)
        daily_dose     = sched.standard_qty / sched.cycle_days if sched.cycle_days else 1
        days_remaining = int(qty_remaining / daily_dose) if daily_dose > 0 else None
        predicted_runout = (
            (today + timedelta(days=days_remaining)).isoformat()
            if days_remaining is not None
            else None
        )

        result.append({
            "drug_id":            drug.id,
            "generic_name":       drug.generic_name,
            "brand_name":         drug.brand_name,
            "strength":           drug.strength,
            "dosage_form":        drug.dosage_form,
            "cycle_days":         sched.cycle_days,
            "standard_qty":       sched.standard_qty,
            "qty_in_stock":       qty_remaining,
            "last_refill_date":   sched.last_refill_date.isoformat() if sched.last_refill_date else None,
            "next_refill_date":   sched.next_refill_date.isoformat() if sched.next_refill_date else None,
            "predicted_runout_date": predicted_runout,
        })

    return result


# ─────────────────────────────────────────────
# /health-card — STRUCTURED JSON FOR QR CODE
# ─────────────────────────────────────────────

def get_health_card(patient_id: int, db: Session) -> dict:
    patient = _get_patient_or_404(patient_id, db)

    allergies = (
        db.query(PatientAllergy)
        .filter(PatientAllergy.patient_id == patient_id)
        .all()
    )

    active_schedules = (
        db.query(RefillSchedule)
        .filter(RefillSchedule.patient_id == patient_id, RefillSchedule.is_active == True)
        .all()
    )

    meds = []
    for sched in active_schedules:
        drug = db.query(Drug).filter(Drug.id == sched.drug_id).first()
        if drug:
            meds.append({
                "drug_id":      drug.id,
                "generic_name": drug.generic_name,
                "strength":     drug.strength,
            })

    return {
        "patient_id":   patient.id,
        "full_name":    patient.full_name,
        "allergies": [
            {"allergen": a.allergen, "severity": a.severity}
            for a in allergies
        ],
        "current_medications": meds,
        "generated_at": date.today().isoformat(),
    }


# ─────────────────────────────────────────────
# /allergies — ADD / DELETE
# ─────────────────────────────────────────────

VALID_SEVERITIES = {"mild", "moderate", "severe"}


def add_allergy(patient_id: int, allergen: str, severity: str, db: Session) -> dict:
    _get_patient_or_404(patient_id, db)

    allergen = allergen.strip()
    severity = severity.strip().lower()

    if not allergen:
        raise HTTPException(status_code=400, detail="Allergen cannot be empty.")
    if severity not in VALID_SEVERITIES:
        raise HTTPException(
            status_code=400,
            detail=f"severity must be one of: {', '.join(VALID_SEVERITIES)}",
        )

    allergy = PatientAllergy(
        patient_id=patient_id,
        allergen=allergen,
        severity=severity,
    )
    db.add(allergy)
    db.commit()
    db.refresh(allergy)

    return {
        "id":         allergy.id,
        "patient_id": allergy.patient_id,
        "allergen":   allergy.allergen,
        "severity":   allergy.severity,
        "added_at":   allergy.added_at.isoformat(),
    }


def delete_allergy(patient_id: int, allergy_id: int, db: Session) -> dict:
    allergy = (
        db.query(PatientAllergy)
        .filter(PatientAllergy.id == allergy_id, PatientAllergy.patient_id == patient_id)
        .first()
    )
    if not allergy:
        raise HTTPException(status_code=404, detail="Allergy not found.")

    db.delete(allergy)
    db.commit()
    return {"detail": f"Allergy id={allergy_id} removed."}


# ─────────────────────────────────────────────
# /refill-request — CREATE + LIST
# ─────────────────────────────────────────────

VALID_REFILL_STATUSES = {"pending", "approved", "dispensed", "cancelled"}


def create_refill_request(
    patient_id: int,
    drug_id: int,
    notes: Optional[str],
    db: Session,
) -> dict:
    patient = _get_patient_or_404(patient_id, db)

    drug = db.query(Drug).filter(Drug.id == drug_id, Drug.is_active == True).first()
    if not drug:
        raise HTTPException(status_code=404, detail=f"Drug id={drug_id} not found.")

    # Check patient actually has this drug on their schedule (optional safety check)
    on_schedule = (
        db.query(RefillSchedule)
        .filter(
            RefillSchedule.patient_id == patient_id,
            RefillSchedule.drug_id == drug_id,
            RefillSchedule.is_active == True,
        )
        .first()
    )
    if not on_schedule:
        raise HTTPException(
            status_code=400,
            detail="This drug is not on your active medication schedule.",
        )

    req = RefillRequest(
        patient_id=patient_id,
        drug_id=drug_id,
        status="pending",
        notes=notes,
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    # Notify pharmacy via WhatsApp (to the pharmacy's configured number or a staff phone)
    # We send the notification to the patient's own phone as an acknowledgement,
    # and log the pharmacist notification — actual staff WhatsApp notify would use
    # a staff phone from settings; for now we message the patient.
    msg = (
        f"✅ *Refill Request Received*\n\n"
        f"Hi {patient.full_name.split()[0]}, your refill request for "
        f"*{drug.generic_name} ({drug.strength})* has been submitted.\n"
        f"Request ID: #{req.id}\n"
        f"Status: *Pending*\n\n"
        f"We will notify you when it is ready. 💊"
    )
    _send_whatsapp(patient.phone_number, msg)

    logger.info(f"Refill request #{req.id} created for patient_id={patient_id} drug_id={drug_id}")
    return {
        "id":           req.id,
        "patient_id":   req.patient_id,
        "drug_id":      req.drug_id,
        "drug_name":    drug.generic_name,
        "status":       req.status,
        "requested_at": req.requested_at.isoformat(),
        "notes":        req.notes,
    }


def list_refill_requests(patient_id: int, db: Session) -> list:
    _get_patient_or_404(patient_id, db)

    requests = (
        db.query(RefillRequest)
        .filter(RefillRequest.patient_id == patient_id)
        .order_by(RefillRequest.requested_at.desc())
        .all()
    )

    result = []
    for req in requests:
        drug = db.query(Drug).filter(Drug.id == req.drug_id).first()
        result.append({
            "id":           req.id,
            "drug_id":      req.drug_id,
            "drug_name":    drug.generic_name if drug else None,
            "strength":     drug.strength if drug else None,
            "status":       req.status,
            "requested_at": req.requested_at.isoformat(),
            "notes":        req.notes,
        })

    return result
