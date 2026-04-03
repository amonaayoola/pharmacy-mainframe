"""
Background Scheduler — The Subconscious
Runs nightly without human intervention:
  1. FX Rate Sync + Volatility Oracle  — every 6 hours
  2. Refill Outreach                   — daily at 09:00
  3. Expiry Watchdog                   — daily at 06:00
  4. Auto-PO Engine                    — daily at 07:00
  5. Stock Velocity                    — daily at midnight
"""

import asyncio
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.fx_service import fetch_live_fx_rate, PricingEngine
from app.services.whatsapp_service import whatsapp_service
from app.models.models import (
    StockBatch, StockStatus, RefillSchedule, Patient,
    Drug, PurchaseOrder, ProcurementLine, POStatus, FXRate, FXAlert
)

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Africa/Lagos")


# ─────────────────────────────────────────────
# CLAUDE FX VOLATILITY ORACLE
# Called by job_fx_sync when a >=2% swing is detected
# ─────────────────────────────────────────────

async def run_claude_fx_analysis(
    prev_rate: float,
    new_rate: float,
    change_pct: float,
    direction: str,
    db,
) -> None:
    """
    Calls Claude to generate an actionable repricing advisory.
    Stores the result in the fx_alerts table (PostgreSQL — same DB as everything else).
    Uses httpx (async) so it never blocks the event loop.
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("⚠️  [FX ORACLE] ANTHROPIC_API_KEY not set — skipping Claude analysis")
        return

    if not settings.FX_ALERT_ENABLED:
        logger.info("[FX ORACLE] FX alerts disabled in config — skipping")
        return

    # Fetch all active drugs so Claude has full context
    drugs = db.query(Drug).filter(Drug.is_active == True).all()
    drug_summary = "\n".join(
        f"- {d.brand_name} ({d.generic_name} {d.strength or ''}): "
        f"cost ${float(d.cost_usd):.2f} USD | class: {d.drug_class or 'unclassified'} | "
        f"tags: {', '.join(d.tags or [])}"
        for d in drugs
    )
    drugs_count = len(drugs)

    prompt = f"""You are a pharmaceutical supply chain analyst advising a Nigerian retail pharmacy (Lagos).

SITUATION:
The Naira has just {'devalued' if direction == 'devaluation' else 'appreciated'} {change_pct:.2f}% against the USD.
- Previous rate: ₦{prev_rate:,.0f} / USD
- New rate:      ₦{new_rate:,.0f} / USD
- Direction:     {direction.upper()}
- Target margin: 25% (all drugs are imported and priced in USD)

PHARMACY DRUG INVENTORY ({drugs_count} active drugs):
{drug_summary}

TASK:
1. Identify the 5 drug categories from this inventory most urgently needing repricing. Rank by: (a) how price-sensitive the patient population is, (b) how thin the margin is likely to be after this FX move, (c) whether the drug is a chronic/essential medication.
2. For each category, give: the specific drug(s) affected from the list above, the estimated margin erosion in Naira per unit (assume 25% margin target), and one clear action.
3. Give a one-paragraph executive summary suitable for a pharmacy dashboard alert.

Keep the response professional, specific to Nigerian pharmacy context, and under 400 words. Use plain text only — no markdown headers or bullet symbols."""

    logger.info(f"[FX ORACLE] Calling Claude (claude-sonnet-4-6) for FX analysis...")

    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            data = response.json()
            analysis_text = data["content"][0]["text"]

        # Persist the alert in YOUR PostgreSQL database
        alert = FXAlert(
            prev_rate=Decimal(str(round(prev_rate, 2))),
            new_rate=Decimal(str(round(new_rate, 2))),
            change_pct=Decimal(str(round(change_pct, 3))),
            direction=direction,
            claude_analysis=analysis_text,
            drugs_affected_count=drugs_count,
            model_used="claude-sonnet-4-6",
        )
        db.add(alert)
        db.commit()

        logger.info(
            f"✅ [FX ORACLE] Alert saved (ID: {alert.id}) — "
            f"{change_pct:.2f}% {direction} | ₦{prev_rate:,.0f} → ₦{new_rate:,.0f}"
        )
        logger.info(f"[FX ORACLE] Claude says:\n{analysis_text[:300]}...")

    except httpx.HTTPStatusError as e:
        logger.error(f"❌ [FX ORACLE] Anthropic API error {e.response.status_code}: {e.response.text}")
    except httpx.RequestError as e:
        logger.error(f"❌ [FX ORACLE] Network error calling Anthropic: {e}")
    except Exception as e:
        logger.error(f"❌ [FX ORACLE] Unexpected error: {e}", exc_info=True)


# ─────────────────────────────────────────────
# JOB 1: FX RATE SYNC + VOLATILITY ORACLE
# ─────────────────────────────────────────────

async def job_fx_sync():
    """
    Fetch live NGN/USD rate from AbokiFX, persist it, then:
    - Compare against the previous recorded rate
    - If swing >= FX_VOLATILITY_THRESHOLD_PCT (default 2%), trigger Claude analysis
    - Store Claude's advisory in fx_alerts table
    All reads/writes go to your existing PostgreSQL — no external DB.
    """
    logger.info("⏰ [SCHEDULER] FX Sync + Volatility Oracle starting...")
    db = SessionLocal()
    try:
        # 1. Fetch current rate (async httpx — never blocks the event loop)
        new_rate = await fetch_live_fx_rate()

        # 2. Get the previous rate from YOUR PostgreSQL fx_rates table
        prev_record = (
            db.query(FXRate)
            .order_by(FXRate.recorded_at.desc())
            .first()
        )

        # 3. Record new rate
        db.add(FXRate(usd_ngn=new_rate, source="AbokiFX"))
        db.commit()
        logger.info(f"✅ [FX] Rate recorded: ₦{new_rate:,.2f} / 1 USD")

        # 4. Volatility check — only if we have a previous rate to compare against
        if prev_record:
            prev_rate = float(prev_record.usd_ngn)
            change_pct = abs((new_rate - prev_rate) / prev_rate) * 100
            threshold = settings.FX_VOLATILITY_THRESHOLD_PCT

            logger.info(
                f"[FX] Previous rate: ₦{prev_rate:,.2f} | "
                f"Change: {change_pct:.3f}% | Threshold: {threshold}%"
            )

            if change_pct >= threshold:
                direction = "devaluation" if new_rate > prev_rate else "appreciation"
                logger.warning(
                    f"⚡ [FX ORACLE] {threshold}% threshold breached! "
                    f"{change_pct:.2f}% {direction} detected. "
                    f"₦{prev_rate:,.0f} → ₦{new_rate:,.0f}"
                )
                # Trigger Claude — runs async, won't block other scheduler jobs
                await run_claude_fx_analysis(
                    prev_rate=prev_rate,
                    new_rate=new_rate,
                    change_pct=change_pct,
                    direction=direction,
                    db=db,
                )
            else:
                logger.info(
                    f"[FX] No significant swing detected ({change_pct:.3f}% < {threshold}%). "
                    "No alert triggered."
                )
        else:
            logger.info("[FX] No previous rate in DB — baseline established. Oracle active from next run.")

    except Exception as e:
        logger.error(f"❌ [FX] Sync failed: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()


# ─────────────────────────────────────────────
# JOB 2: REFILL OUTREACH
# Sends WhatsApp reminders to patients whose refill is due in N days
# ─────────────────────────────────────────────

async def job_refill_outreach():
    """
    Query patient_profiles for anyone whose next_refill_date
    falls within the next REFILL_REMINDER_DAYS days.
    Send WhatsApp reminder.
    """
    logger.info("⏰ [SCHEDULER] Refill outreach starting...")
    db = SessionLocal()
    try:
        today = date.today()
        cutoff = today + timedelta(days=settings.REFILL_REMINDER_DAYS)

        due_schedules = (
            db.query(RefillSchedule)
            .join(Patient)
            .join(Drug)
            .filter(
                RefillSchedule.is_active == True,
                RefillSchedule.next_refill_date <= cutoff,
                RefillSchedule.next_refill_date >= today,
                Patient.whatsapp_opted_in == True,
            )
            .all()
        )

        logger.info(f"Found {len(due_schedules)} patients due for refill reminders.")

        from app.services.fx_service import get_cached_fx_rate
        engine = PricingEngine(fx_rate=get_cached_fx_rate())

        sent_count = 0
        for schedule in due_schedules:
            patient = schedule.patient
            drug = schedule.drug
            days_left = (schedule.next_refill_date - today).days
            price = engine.retail_price_ngn(float(drug.cost_usd)) * schedule.standard_qty

            try:
                await whatsapp_service.send_refill_reminder(
                    patient_name=patient.full_name,
                    phone=patient.phone_number,
                    drug_name=f"{drug.brand_name} ({drug.strength})",
                    days_left=days_left,
                    price_ngn=price,
                )
                sent_count += 1
                logger.info(f"✅ Refill reminder sent to {patient.full_name} ({patient.phone_number})")
            except Exception as e:
                logger.warning(f"⚠️ Failed to send to {patient.phone_number}: {e}")

        logger.info(f"✅ [REFILL] {sent_count}/{len(due_schedules)} reminders sent")
    except Exception as e:
        logger.error(f"❌ [REFILL] Outreach failed: {e}", exc_info=True)
    finally:
        db.close()


# ─────────────────────────────────────────────
# JOB 3: EXPIRY WATCHDOG
# ─────────────────────────────────────────────

async def job_expiry_watchdog():
    """
    Scan all stock batches for items expiring within EXPIRY_WARN_DAYS.
    - Promote items < 90 days → Promotion status in POS
    - Flag items < 30 days as CRITICAL
    - Write off expired items
    """
    logger.info("⏰ [SCHEDULER] Expiry watchdog starting...")
    db = SessionLocal()
    try:
        today = date.today()
        warn_cutoff = today + timedelta(days=settings.EXPIRY_WARN_DAYS)
        critical_cutoff = today + timedelta(days=30)

        batches = db.query(StockBatch).filter(StockBatch.quantity > 0).all()
        promoted, flagged, expired_written = 0, 0, 0

        for batch in batches:
            if batch.expiry_date < today:
                # Expired — write off
                batch.status = StockStatus.expired
                batch.quantity = 0
                expired_written += 1
                logger.warning(f"EXPIRED: {batch.batch_no} written off")

            elif batch.expiry_date <= critical_cutoff:
                batch.status = StockStatus.promotion
                flagged += 1

            elif batch.expiry_date <= warn_cutoff:
                if batch.status != StockStatus.promotion:
                    batch.status = StockStatus.promotion
                    promoted += 1

        db.commit()
        logger.info(
            f"✅ [EXPIRY] Written off: {expired_written} | "
            f"Promoted: {promoted} | Critical: {flagged}"
        )
    except Exception as e:
        logger.error(f"❌ [EXPIRY] Watchdog failed: {e}", exc_info=True)
    finally:
        db.close()


# ─────────────────────────────────────────────
# JOB 4: AUTO-PROCUREMENT ENGINE
# Generates POs before stock runs out
# ─────────────────────────────────────────────

async def job_auto_procurement():
    """
    For each drug, calculate days of stock remaining based on burn rate.
    If days_remaining < AUTO_PO_THRESHOLD_DAYS, generate a draft Purchase Order.
    """
    if not settings.AUTO_PO_ENABLED:
        logger.info("[AUTO-PO] Disabled in config. Skipping.")
        return

    logger.info("⏰ [SCHEDULER] Auto-procurement engine starting...")
    db = SessionLocal()
    try:
        from app.services.fx_service import get_cached_fx_rate
        fx_rate = get_cached_fx_rate()

        # Get all drugs with their current stock totals and burn rates
        drugs = db.query(Drug).filter(Drug.is_active == True).all()
        po_lines_needed = []

        for drug in drugs:
            total_qty = sum(
                b.quantity for b in drug.stock_batches
                if b.status not in [StockStatus.expired, StockStatus.out]
            )

            # Calculate burn rate from last 7 days of transactions
            # (simplified — in production query stock_transactions)
            # For now use the drug's known average burn rate
            avg_burn_per_day = _estimate_burn_rate(db, drug.id)

            if avg_burn_per_day <= 0:
                continue

            days_remaining = total_qty / avg_burn_per_day

            if days_remaining < settings.AUTO_PO_THRESHOLD_DAYS:
                # Order 30 days worth
                qty_to_order = int(avg_burn_per_day * 30)
                po_lines_needed.append({
                    "drug": drug,
                    "qty": qty_to_order,
                    "days_remaining": round(days_remaining, 1),
                })
                logger.warning(
                    f"⚠️ LOW STOCK: {drug.brand_name} — {days_remaining:.1f}d remaining. "
                    f"Auto-PO: {qty_to_order} units."
                )

        if po_lines_needed:
            # Create a draft PO (assign to first active wholesaler)
            from app.models.models import Wholesaler
            wholesaler = db.query(Wholesaler).filter(Wholesaler.is_active == True).first()
            if not wholesaler:
                logger.warning("No active wholesaler configured. Cannot create auto-PO.")
                return

            po = PurchaseOrder(
                wholesaler_id=wholesaler.id,
                status=POStatus.draft,
                fx_rate=fx_rate,
                auto_generated=True,
                expected_delivery=date.today() + timedelta(days=wholesaler.lead_time_days),
                notes=f"Auto-generated by Mainframe on {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            db.add(po)
            db.flush()

            total_usd = 0
            for item in po_lines_needed:
                cost = float(item["drug"].cost_usd) * item["qty"]
                total_usd += cost
                line = ProcurementLine(
                    po_id=po.id,
                    drug_id=item["drug"].id,
                    quantity_ordered=item["qty"],
                    unit_cost_usd=item["drug"].cost_usd,
                    total_usd=cost,
                )
                db.add(line)

            po.total_usd = total_usd
            po.total_ngn = total_usd * fx_rate
            db.commit()
            logger.info(
                f"✅ [AUTO-PO] Created PO #{po.id} — "
                f"{len(po_lines_needed)} lines — ${total_usd:,.2f} USD"
            )

    except Exception as e:
        logger.error(f"❌ [AUTO-PO] Engine failed: {e}", exc_info=True)
    finally:
        db.close()


def _estimate_burn_rate(db, drug_id: int) -> float:
    """
    Estimate daily burn rate from the last 7 days of transactions.
    Falls back to a sensible default if no data.
    """
    from sqlalchemy import func as sqlfunc
    from app.models.models import StockTransaction, TransactionType, StockBatch

    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    result = (
        db.query(sqlfunc.sum(StockTransaction.quantity_change))
        .join(StockBatch, StockBatch.id == StockTransaction.batch_id)
        .filter(
            StockBatch.drug_id == drug_id,
            StockTransaction.transaction_type == TransactionType.sale,
            StockTransaction.created_at >= seven_days_ago,
        )
        .scalar()
    )
    total_sold = abs(result or 0)
    return total_sold / 7 if total_sold > 0 else 0


# ─────────────────────────────────────────────
# SCHEDULER SETUP
# ─────────────────────────────────────────────

def start_scheduler():
    """Register all jobs and start the APScheduler."""

    # FX Sync — every 6 hours
    scheduler.add_job(
        job_fx_sync,
        trigger=IntervalTrigger(hours=settings.FX_UPDATE_INTERVAL_HOURS),
        id="fx_sync",
        name="FX Rate Sync",
        replace_existing=True,
    )

    # Refill Outreach — daily at 09:00 Lagos time
    scheduler.add_job(
        job_refill_outreach,
        trigger=CronTrigger(hour=9, minute=0, timezone="Africa/Lagos"),
        id="refill_outreach",
        name="Refill WhatsApp Outreach",
        replace_existing=True,
    )

    # Expiry Watchdog — daily at 06:00
    scheduler.add_job(
        job_expiry_watchdog,
        trigger=CronTrigger(hour=6, minute=0, timezone="Africa/Lagos"),
        id="expiry_watchdog",
        name="Expiry Watchdog",
        replace_existing=True,
    )

    # Auto-Procurement — daily at 07:00
    scheduler.add_job(
        job_auto_procurement,
        trigger=CronTrigger(hour=7, minute=0, timezone="Africa/Lagos"),
        id="auto_procurement",
        name="Auto-Procurement Engine",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("✅ Scheduler started with 4 background jobs")
    logger.info("   - FX Sync: every 6 hours")
    logger.info("   - Refill Outreach: daily at 09:00 WAT")
    logger.info("   - Expiry Watchdog: daily at 06:00 WAT")
    logger.info("   - Auto-Procurement: daily at 07:00 WAT")
