"""drugs.py — Drug registry CRUD"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from app.core.database import get_db
from app.models.models import Drug
from app.services.fx_service import PricingEngine, get_cached_fx_rate

router = APIRouter()

class DrugCreate(BaseModel):
    generic_name: str
    brand_name: str
    strength: Optional[str] = None
    dosage_form: Optional[str] = None
    nafdac_reg_no: Optional[str] = None
    manufacturer: Optional[str] = None
    drug_class: Optional[str] = None
    tags: Optional[List[str]] = []
    requires_prescription: bool = False
    is_controlled: bool = False
    clinical_flags: Optional[dict] = {}
    cost_usd: float

class DrugOut(BaseModel):
    id: int
    generic_name: str
    brand_name: str
    strength: Optional[str]
    dosage_form: Optional[str]
    nafdac_reg_no: Optional[str]
    manufacturer: Optional[str]
    drug_class: Optional[str]
    tags: Optional[List[str]]
    requires_prescription: bool
    cost_usd: float
    retail_ngn: Optional[float] = None
    is_active: bool

    class Config:
        from_attributes = True

@router.get("/", response_model=List[DrugOut])
def list_drugs(
    search: Optional[str] = Query(None),
    drug_class: Optional[str] = None,
    db: Session = Depends(get_db)
):
    q = db.query(Drug).filter(Drug.is_active == True)
    if search:
        q = q.filter(
            Drug.brand_name.ilike(f"%{search}%") |
            Drug.generic_name.ilike(f"%{search}%")
        )
    if drug_class:
        q = q.filter(Drug.drug_class == drug_class)
    drugs = q.all()
    engine = PricingEngine(fx_rate=get_cached_fx_rate())
    for drug in drugs:
        drug.retail_ngn = engine.retail_price_ngn(float(drug.cost_usd))
    return drugs

@router.post("/", response_model=DrugOut, status_code=201)
def create_drug(drug_in: DrugCreate, db: Session = Depends(get_db)):
    drug = Drug(**drug_in.dict())
    db.add(drug)
    db.commit()
    db.refresh(drug)
    return drug

@router.get("/{drug_id}", response_model=DrugOut)
def get_drug(drug_id: int, db: Session = Depends(get_db)):
    drug = db.query(Drug).filter(Drug.id == drug_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")
    drug.retail_ngn = PricingEngine(fx_rate=get_cached_fx_rate()).retail_price_ngn(float(drug.cost_usd))
    return drug

@router.delete("/{drug_id}", status_code=204)
def deactivate_drug(drug_id: int, db: Session = Depends(get_db)):
    drug = db.query(Drug).filter(Drug.id == drug_id).first()
    if not drug:
        raise HTTPException(404, "Drug not found")
    drug.is_active = False
    db.commit()
