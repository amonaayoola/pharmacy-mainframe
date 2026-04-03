"""patients.py"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date, timedelta
from app.core.database import get_db
from app.models.models import Patient, RefillSchedule, Drug

router = APIRouter()

class PatientCreate(BaseModel):
    full_name: str
    phone_number: str
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    address: Optional[str] = None
    condition_tags: Optional[List[str]] = []
    allergies: Optional[List[str]] = []
    notes: Optional[str] = None
    whatsapp_opted_in: bool = True

class RefillScheduleCreate(BaseModel):
    patient_id: int
    drug_id: int
    cycle_days: int = 30
    standard_qty: int = 30
    last_refill_date: date

@router.get("/")
def list_patients(db: Session = Depends(get_db)):
    patients = db.query(Patient).filter(Patient.is_active == True).all()
    today = date.today()
    result = []
    for p in patients:
        schedules = db.query(RefillSchedule).filter(
            RefillSchedule.patient_id == p.id,
            RefillSchedule.is_active == True
        ).all()
        result.append({
            "id": p.id,
            "full_name": p.full_name,
            "phone_number": p.phone_number,
            "condition_tags": p.condition_tags,
            "allergies": p.allergies,
            "whatsapp_opted_in": p.whatsapp_opted_in,
            "refill_schedules": [
                {
                    "drug_id": s.drug_id,
                    "cycle_days": s.cycle_days,
                    "next_refill_date": s.next_refill_date,
                    "days_until_refill": (s.next_refill_date - today).days if s.next_refill_date else None,
                    "is_due": s.next_refill_date and (s.next_refill_date - today).days <= 3,
                }
                for s in schedules
            ],
        })
    return result

@router.post("/", status_code=201)
def create_patient(patient_in: PatientCreate, db: Session = Depends(get_db)):
    existing = db.query(Patient).filter(Patient.phone_number == patient_in.phone_number).first()
    if existing:
        raise HTTPException(400, f"Patient with phone {patient_in.phone_number} already exists")
    patient = Patient(**patient_in.dict())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return {"id": patient.id, "full_name": patient.full_name, "phone_number": patient.phone_number}

@router.post("/refill-schedules", status_code=201)
def create_refill_schedule(schedule_in: RefillScheduleCreate, db: Session = Depends(get_db)):
    next_date = schedule_in.last_refill_date + timedelta(days=schedule_in.cycle_days)
    schedule = RefillSchedule(
        patient_id=schedule_in.patient_id,
        drug_id=schedule_in.drug_id,
        cycle_days=schedule_in.cycle_days,
        standard_qty=schedule_in.standard_qty,
        last_refill_date=schedule_in.last_refill_date,
        next_refill_date=next_date,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return {"id": schedule.id, "next_refill_date": next_date}

@router.get("/due-refills")
def get_due_refills(days: int = 3, db: Session = Depends(get_db)):
    today = date.today()
    cutoff = today + timedelta(days=days)
    schedules = (
        db.query(RefillSchedule)
        .filter(
            RefillSchedule.is_active == True,
            RefillSchedule.next_refill_date <= cutoff,
            RefillSchedule.next_refill_date >= today,
        )
        .all()
    )
    return [
        {
            "patient_id": s.patient_id,
            "patient_name": s.patient.full_name,
            "phone": s.patient.phone_number,
            "drug_id": s.drug_id,
            "drug_name": s.drug.brand_name if s.drug else "Unknown",
            "next_refill_date": s.next_refill_date,
            "days_until": (s.next_refill_date - today).days,
        }
        for s in schedules
    ]
