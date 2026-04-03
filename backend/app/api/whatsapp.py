"""whatsapp.py"""
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.services.whatsapp_service import whatsapp_service
from app.models.models import Patient, WhatsAppMessage

router = APIRouter()

class SendMessageRequest(BaseModel):
    patient_id: int
    message: str

class RefillReminderRequest(BaseModel):
    patient_id: int
    drug_name: str
    days_left: int
    price_ngn: float

@router.post("/send")
async def send_message(req: SendMessageRequest, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.id == req.patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    result = await whatsapp_service.send_message(patient.phone_number, req.message)
    log = WhatsAppMessage(patient_id=patient.id, direction="outbound",
                          body=req.message, wa_message_id=result.get("wa_message_id"),
                          status=result.get("status", "sent"))
    db.add(log)
    db.commit()
    return result

@router.post("/refill-reminder")
async def send_refill_reminder(req: RefillReminderRequest, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.id == req.patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return await whatsapp_service.send_refill_reminder(
        patient.full_name, patient.phone_number, req.drug_name, req.days_left, req.price_ngn)

@router.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        data = await request.json()
        messages = data.get("entry",[{}])[0].get("changes",[{}])[0].get("value",{}).get("messages",[])
        for msg in messages:
            phone = f"+{msg.get('from')}"
            body = msg.get("text",{}).get("body","")
            patient = db.query(Patient).filter(Patient.phone_number == phone).first()
            if patient:
                db.add(WhatsAppMessage(patient_id=patient.id, direction="inbound",
                                       body=body, wa_message_id=msg.get("id"), status="received"))
        db.commit()
        return {"status": "received"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == "mainframe_verify_token":
        return int(params.get("hub.challenge", 0))
    raise HTTPException(403, "Invalid verify token")
