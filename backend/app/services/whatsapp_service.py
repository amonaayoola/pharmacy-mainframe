"""
WhatsApp Bot Service
Automated refill reminders, health consultations, and delivery scheduling.
Supports: Meta Cloud API (primary) | Twilio (fallback)
"""

import httpx
import logging
from typing import Optional, List
from datetime import date, timedelta
from app.core.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# MESSAGE TEMPLATES
# ─────────────────────────────────────────────

def refill_reminder_message(patient_name: str, drug_name: str, days_left: int, price_ngn: float, branch_name: str) -> str:
    greeting = patient_name.split()[0]  # First name only
    return (
        f"Hello {greeting} 👋\n\n"
        f"This is *HealthBridge Pharmacy* ({branch_name}).\n\n"
        f"Your *{drug_name}* refill is due in *{days_left} day{'s' if days_left != 1 else ''}*.\n\n"
        f"Current price: *₦{price_ngn:,.0f}* (30-day supply)\n\n"
        f"Reply:\n"
        f"*YES* — Prepare for pickup\n"
        f"*DELIVER* — Home delivery (+₦500)\n"
        f"*LATER* — Remind me tomorrow\n\n"
        f"_Powered by Pharmacy Mainframe_ ✅"
    )


def delivery_confirmation_message(patient_name: str, drug_name: str, total_ngn: float, eta: str) -> str:
    return (
        f"📦 *Delivery Confirmed!*\n\n"
        f"Hi {patient_name.split()[0]},\n"
        f"Your *{drug_name}* is on its way.\n\n"
        f"• Estimated arrival: *{eta}*\n"
        f"• Total to pay: *₦{total_ngn:,.0f}* (cash on delivery)\n\n"
        f"Please have your Mainframe Verified QR receipt ready.\n"
        f"Reply *CANCEL* to cancel delivery.\n\n"
        f"_HealthBridge Pharmacy_ 💊"
    )


def pickup_ready_message(patient_name: str, drug_name: str, total_ngn: float) -> str:
    return (
        f"✅ *Ready for Pickup!*\n\n"
        f"Hi {patient_name.split()[0]},\n"
        f"Your *{drug_name}* is ready at our pharmacy.\n\n"
        f"Amount due: *₦{total_ngn:,.0f}*\n"
        f"Opening hours: Mon–Sat 8am–8pm | Sun 10am–4pm\n\n"
        f"_HealthBridge Pharmacy_ 💊"
    )


def price_change_alert_message(patient_name: str, drug_name: str, old_price: float, new_price: float) -> str:
    direction = "increased" if new_price > old_price else "decreased"
    emoji = "📈" if new_price > old_price else "📉"
    return (
        f"{emoji} *Price Update*\n\n"
        f"Hi {patient_name.split()[0]},\n"
        f"The price of *{drug_name}* has {direction} due to Naira exchange rate movement.\n\n"
        f"• Previous price: ₦{old_price:,.0f}\n"
        f"• New price: *₦{new_price:,.0f}*\n\n"
        f"Reply *REFILL* to order at the new price.\n\n"
        f"_HealthBridge Pharmacy_ 💊"
    )


def drug_interaction_warning_message(patient_name: str, drug_a: str, drug_b: str, message: str) -> str:
    return (
        f"⚠️ *Medication Safety Alert*\n\n"
        f"Hi {patient_name.split()[0]},\n"
        f"Our pharmacist has flagged a potential interaction between:\n\n"
        f"• *{drug_a}*\n"
        f"• *{drug_b}*\n\n"
        f"_{message}_\n\n"
        f"Please *do not take both medications together* until you speak with our pharmacist.\n\n"
        f"📞 Call us: 01-555-0100\n\n"
        f"_HealthBridge Pharmacy — Your safety is our priority_ 💊"
    )


# ─────────────────────────────────────────────
# WHATSAPP API CLIENT
# ─────────────────────────────────────────────

class WhatsAppService:
    """
    Sends WhatsApp messages via Meta Cloud API.
    Falls back to Twilio if Meta is not configured.
    """

    def __init__(self):
        self.provider = settings.WHATSAPP_PROVIDER
        self.meta_phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self.meta_token = settings.WHATSAPP_ACCESS_TOKEN
        self.twilio_sid = settings.TWILIO_ACCOUNT_SID
        self.twilio_token = settings.TWILIO_AUTH_TOKEN
        self.twilio_from = settings.TWILIO_WHATSAPP_FROM

    async def send_message(self, to_phone: str, message: str) -> dict:
        """
        Send a WhatsApp text message.
        to_phone: international format, e.g. "+2348021112222"
        """
        # Normalise phone number
        phone = to_phone.strip().replace(" ", "").replace("-", "")
        if not phone.startswith("+"):
            phone = "+234" + phone.lstrip("0")

        if self.provider == "meta" and self.meta_token:
            return await self._send_via_meta(phone, message)
        elif self.twilio_sid:
            return await self._send_via_twilio(phone, message)
        else:
            # Development mode — log only
            logger.info(f"[DEV] WhatsApp to {phone}: {message[:80]}...")
            return {"status": "dev_mode", "phone": phone, "preview": message[:80]}

    async def _send_via_meta(self, phone: str, message: str) -> dict:
        """Meta Cloud API — https://graph.facebook.com/v18.0/"""
        url = f"https://graph.facebook.com/v18.0/{self.meta_phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.meta_token}",
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                wa_id = data.get("messages", [{}])[0].get("id")
                logger.info(f"Meta WhatsApp sent: {wa_id} → {phone}")
                return {"status": "sent", "provider": "meta", "wa_message_id": wa_id}
        except httpx.HTTPError as e:
            logger.error(f"Meta WhatsApp failed: {e}")
            raise

    async def _send_via_twilio(self, phone: str, message: str) -> dict:
        """Twilio WhatsApp Business API."""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_sid}/Messages.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    data={
                        "From": self.twilio_from,
                        "To": f"whatsapp:{phone}",
                        "Body": message,
                    },
                    auth=(self.twilio_sid, self.twilio_token),
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"Twilio WhatsApp sent: {data['sid']} → {phone}")
                return {"status": "sent", "provider": "twilio", "wa_message_id": data["sid"]}
        except httpx.HTTPError as e:
            logger.error(f"Twilio WhatsApp failed: {e}")
            raise

    async def send_refill_reminder(self, patient_name: str, phone: str,
                                    drug_name: str, days_left: int,
                                    price_ngn: float) -> dict:
        msg = refill_reminder_message(
            patient_name, drug_name, days_left, price_ngn,
            branch_name="Lagos Island Branch"
        )
        return await self.send_message(phone, msg)

    async def send_delivery_confirmation(self, patient_name: str, phone: str,
                                          drug_name: str, total_ngn: float,
                                          eta: str = "3:00 PM today") -> dict:
        msg = delivery_confirmation_message(patient_name, drug_name, total_ngn, eta)
        return await self.send_message(phone, msg)

    async def send_price_alert(self, patient_name: str, phone: str,
                                drug_name: str, old_price: float, new_price: float) -> dict:
        msg = price_change_alert_message(patient_name, drug_name, old_price, new_price)
        return await self.send_message(phone, msg)

    async def send_drug_interaction_warning(self, patient_name: str, phone: str,
                                             drug_a: str, drug_b: str, interaction_msg: str) -> dict:
        msg = drug_interaction_warning_message(patient_name, drug_a, drug_b, interaction_msg)
        return await self.send_message(phone, msg)

    def parse_inbound_response(self, body: str) -> str:
        """
        Parse patient reply to a refill reminder.
        Returns: "confirm_pickup" | "confirm_delivery" | "postpone" | "cancel" | "unknown"
        """
        text = body.strip().upper()
        if text in ["YES", "Y", "1", "CONFIRM", "OK"]:
            return "confirm_pickup"
        if text in ["DELIVER", "DELIVERY", "2", "D"]:
            return "confirm_delivery"
        if text in ["LATER", "TOMORROW", "3", "REMIND", "NO"]:
            return "postpone"
        if text in ["CANCEL", "STOP", "QUIT"]:
            return "cancel"
        if text in ["REFILL", "ORDER", "BUY"]:
            return "confirm_pickup"
        return "unknown"


# Singleton
whatsapp_service = WhatsAppService()
