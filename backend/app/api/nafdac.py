"""nafdac.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.nafdac_service import nafdac_service
from app.models.models import NAFDACVerification

router = APIRouter()

@router.get("/verify/{batch_no}")
async def verify_batch(batch_no: str, verified_by: str = "pharmacist"):
    return await nafdac_service.verify_batch(batch_no, verified_by=verified_by)

@router.get("/registry")
def registry_summary():
    return nafdac_service.get_local_registry_summary()

@router.get("/history")
def verification_history(limit: int = 20, db: Session = Depends(get_db)):
    records = (
        db.query(NAFDACVerification)
        .order_by(NAFDACVerification.verified_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {"id": r.id, "batch_no": r.batch_no, "result": r.result,
         "verified_by": r.verified_by, "verified_at": r.verified_at}
        for r in records
    ]
