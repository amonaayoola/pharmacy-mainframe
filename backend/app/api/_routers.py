"""pricing.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.models.models import Drug, FXRate
from app.services.fx_service import PricingEngine, get_cached_fx_rate, fetch_live_fx_rate, set_manual_fx_rate

router = APIRouter()

class PriceCalcRequest(BaseModel):
    cost_usd: float
    margin: Optional[float] = 0.25
    fx_rate: Optional[float] = None

@router.post("/calculate")
def calculate_price(req: PriceCalcRequest):
    engine = PricingEngine(fx_rate=req.fx_rate or get_cached_fx_rate(), margin=req.margin)
    return {
        "cost_usd": req.cost_usd,
        "fx_rate": engine.fx_rate,
        "margin_pct": req.margin * 100,
        "landed_ngn": engine.landed_cost_ngn(req.cost_usd),
        "retail_ngn": engine.retail_price_ngn(req.cost_usd),
        "margin_ngn": engine.margin_amount_ngn(req.cost_usd),
    }

@router.get("/all-drugs")
def price_all_drugs(margin: Optional[float] = 0.25, db: Session = Depends(get_db)):
    drugs = db.query(Drug).filter(Drug.is_active == True).all()
    engine = PricingEngine(fx_rate=get_cached_fx_rate(), margin=margin)
    return {
        "fx_rate": engine.fx_rate,
        "margin_pct": margin * 100,
        "prices": engine.price_all_drugs(drugs, margin),
    }

@router.get("/fx-rate")
async def get_fx_rate():
    rate = await fetch_live_fx_rate()
    return {"usd_ngn": rate, "source": "AbokiFX"}

@router.post("/fx-rate/manual")
def set_fx_rate(rate: float):
    set_manual_fx_rate(rate)
    return {"message": f"FX rate manually set to ₦{rate:,.2f}", "usd_ngn": rate}

@router.get("/fx-history")
def fx_history(limit: int = 30, db: Session = Depends(get_db)):
    records = db.query(FXRate).order_by(FXRate.recorded_at.desc()).limit(limit).all()
    return [{"rate": float(r.usd_ngn), "source": r.source, "at": r.recorded_at} for r in records]


"""nafdac.py"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.nafdac_service import nafdac_service
from app.models.models import NAFDACVerification

nafdac_router = APIRouter()

@nafdac_router.get("/verify/{batch_no}")
async def verify_batch(batch_no: str, verified_by: str = "pharmacist"):
    result = await nafdac_service.verify_batch(batch_no, verified_by=verified_by)
    return result

@nafdac_router.get("/registry")
def registry_summary():
    return nafdac_service.get_local_registry_summary()

@nafdac_router.get("/history")
def verification_history(limit: int = 20, db: Session = Depends(get_db)):
    records = (
        db.query(NAFDACVerification)
        .order_by(NAFDACVerification.verified_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "batch_no": r.batch_no,
            "nafdac_reg_no": r.nafdac_reg_no,
            "result": r.result,
            "verified_by": r.verified_by,
            "verified_at": r.verified_at,
        }
        for r in records
    ]

# Export nafdac router with expected name
router = nafdac_router


"""whatsapp.py"""
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from app.core.database import get_db
from app.services.whatsapp_service import whatsapp_service
from app.models.models import Patient, WhatsAppMessage, WhatsAppMessageType

wa_router = APIRouter()

class SendMessageRequest(BaseModel):
    patient_id: int
    message: str

class RefillReminderRequest(BaseModel):
    patient_id: int
    drug_name: str
    days_left: int
    price_ngn: float

@wa_router.post("/send")
async def send_message(req: SendMessageRequest, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.id == req.patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    result = await whatsapp_service.send_message(patient.phone_number, req.message)
    log = WhatsAppMessage(
        patient_id=patient.id,
        direction="outbound",
        body=req.message,
        wa_message_id=result.get("wa_message_id"),
        status=result.get("status", "sent"),
    )
    db.add(log)
    db.commit()
    return result

@wa_router.post("/refill-reminder")
async def send_refill_reminder(req: RefillReminderRequest, db: Session = Depends(get_db)):
    patient = db.query(Patient).filter(Patient.id == req.patient_id).first()
    if not patient:
        raise HTTPException(404, "Patient not found")
    result = await whatsapp_service.send_refill_reminder(
        patient_name=patient.full_name,
        phone=patient.phone_number,
        drug_name=req.drug_name,
        days_left=req.days_left,
        price_ngn=req.price_ngn,
    )
    return result

@wa_router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Receive inbound WhatsApp messages (Meta Webhook).
    Parses patient response and triggers appropriate action.
    """
    try:
        data = await request.json()
        # Meta webhook structure
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        for msg in messages:
            from_phone = msg.get("from")
            body = msg.get("text", {}).get("body", "")
            wa_id = msg.get("id")

            patient = db.query(Patient).filter(Patient.phone_number == f"+{from_phone}").first()
            if patient:
                log = WhatsAppMessage(
                    patient_id=patient.id,
                    direction="inbound",
                    body=body,
                    wa_message_id=wa_id,
                    status="received",
                )
                db.add(log)
                intent = whatsapp_service.parse_inbound_response(body)
                # Queue background task based on intent
                if intent == "confirm_delivery":
                    background_tasks.add_task(
                        whatsapp_service.send_delivery_confirmation,
                        patient.full_name, patient.phone_number,
                        "Your medication", 0, "3:00 PM today"
                    )

        db.commit()
        return {"status": "received"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@wa_router.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook verification challenge."""
    params = dict(request.query_params)
    if params.get("hub.verify_token") == "mainframe_verify_token":
        return int(params.get("hub.challenge", 0))
    raise HTTPException(403, "Invalid verify token")

router = wa_router


"""dashboard.py"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from datetime import date, timedelta, datetime
from app.core.database import get_db
from app.models.models import (
    DispensingRecord, StockBatch, Patient, RefillSchedule,
    PurchaseOrder, POStatus, StockStatus
)
from app.services.fx_service import get_cached_fx_rate

dashboard_router = APIRouter()

@dashboard_router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    today = date.today()
    yesterday = today - timedelta(days=1)

    # Today's revenue
    today_sales = db.query(
        func.coalesce(func.sum(DispensingRecord.total_ngn), 0)
    ).filter(
        func.date(DispensingRecord.created_at) == today,
        DispensingRecord.is_refund == False,
    ).scalar() or 0

    yesterday_sales = db.query(
        func.coalesce(func.sum(DispensingRecord.total_ngn), 0)
    ).filter(
        func.date(DispensingRecord.created_at) == yesterday,
        DispensingRecord.is_refund == False,
    ).scalar() or 1

    # Low stock (expired items or items flagged)
    low_stock_count = db.query(StockBatch).filter(
        StockBatch.status.in_([StockStatus.low, StockStatus.critical, StockStatus.out])
    ).count()

    # Expiring soon
    expiry_cutoff = today + timedelta(days=90)
    expiring_count = db.query(StockBatch).filter(
        StockBatch.expiry_date <= expiry_cutoff,
        StockBatch.expiry_date >= today,
        StockBatch.quantity > 0,
    ).count()

    # Refills due
    refill_cutoff = today + timedelta(days=3)
    refills_due = db.query(RefillSchedule).filter(
        RefillSchedule.is_active == True,
        RefillSchedule.next_refill_date <= refill_cutoff,
        RefillSchedule.next_refill_date >= today,
    ).count()

    # Pending POs
    pending_pos = db.query(PurchaseOrder).filter(
        PurchaseOrder.status.in_([POStatus.draft, POStatus.approved])
    ).count()

    # Transaction count today
    tx_today = db.query(DispensingRecord).filter(
        func.date(DispensingRecord.created_at) == today
    ).count()

    rev_change = float(today_sales) / float(yesterday_sales) * 100 - 100 if yesterday_sales else 0

    return {
        "today": str(today),
        "revenue": {
            "today_ngn": float(today_sales),
            "yesterday_ngn": float(yesterday_sales),
            "change_pct": round(rev_change, 1),
            "transactions": tx_today,
        },
        "inventory": {
            "low_stock_items": low_stock_count,
            "expiring_soon": expiring_count,
        },
        "patients": {
            "refills_due": refills_due,
        },
        "procurement": {
            "pending_pos": pending_pos,
        },
        "fx": {
            "usd_ngn": get_cached_fx_rate(),
            "source": "AbokiFX",
        },
    }

@dashboard_router.get("/revenue-chart")
def revenue_chart(days: int = 7, db: Session = Depends(get_db)):
    """Last N days of daily revenue for the chart."""
    result = []
    for i in range(days - 1, -1, -1):
        d = date.today() - timedelta(days=i)
        revenue = db.query(
            func.coalesce(func.sum(DispensingRecord.total_ngn), 0)
        ).filter(func.date(DispensingRecord.created_at) == d).scalar() or 0
        result.append({"date": str(d), "revenue_ngn": float(revenue)})
    return result

router = dashboard_router
