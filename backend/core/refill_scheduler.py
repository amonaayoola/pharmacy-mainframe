"""
backend/core/refill_scheduler.py
Phase 1D – Daily Refill Job

Runs at 09:00 Lagos time (WAT = UTC+1) every day.
Plug this into your existing APScheduler / Celery beat configuration:

    from backend.core.refill_scheduler import schedule_refill_job
    schedule_refill_job(scheduler)          # APScheduler
    # – OR –
    # app.conf.beat_schedule already contains the Celery entry below.

The job:
    1. Opens a DB session
    2. Identifies all at-risk patients
    3. Groups by patient (may have multiple at-risk drugs)
    4. Bulk-queues WhatsApp reminders via RefillOutreachService
    5. Logs outreach metrics to the job_run_log table
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.services.refill_analytics import AdherenceProfile, RefillAnalyticsEngine
from backend.services.refill_outreach import RefillOutreachService

logger = logging.getLogger(__name__)

# Lagos = West Africa Time  UTC+1
LAGOS_TZ_OFFSET_HOURS = 1
JOB_ID = "daily_refill_check"
JOB_NAME = "Patient Refill Intelligence – Daily Run"


# ---------------------------------------------------------------------------
# Core job function
# ---------------------------------------------------------------------------

def run_daily_refill_job() -> None:
    """
    Entry point called by the scheduler at 09:00 WAT.

    Wraps everything in a try/except so one bad run never crashes the
    scheduler process.  All outcomes (success or error) are written to
    job_run_log.
    """
    started_at = datetime.now(timezone.utc)
    logger.info("[%s] Starting at %s UTC", JOB_NAME, started_at.isoformat())

    db: Session = SessionLocal()
    try:
        analytics = RefillAnalyticsEngine(db)
        outreach = RefillOutreachService(db)

        # 1. Identify all at-risk patients
        at_risk_profiles: list[AdherenceProfile] = analytics.get_at_risk_patients()

        if not at_risk_profiles:
            logger.info("[%s] No patients at risk today – nothing to send.", JOB_NAME)
            _log_run(
                db,
                started_at=started_at,
                status="success",
                patients_identified=0,
                reminders_sent=0,
                reminders_failed=0,
                notes="No at-risk patients.",
            )
            return

        # 2. Group profiles by patient_id
        by_patient: dict[int, list[AdherenceProfile]] = defaultdict(list)
        for profile in at_risk_profiles:
            by_patient[profile.patient_id].append(profile)

        logger.info(
            "[%s] %d at-risk patients found across %d drug profiles.",
            JOB_NAME,
            len(by_patient),
            len(at_risk_profiles),
        )

        # 3. Bulk-queue reminders (one message per patient, listing all at-risk drugs)
        metrics = outreach.bulk_queue(by_patient, channel="whatsapp")

        # 4. Log run outcome
        _log_run(
            db,
            started_at=started_at,
            status="success",
            patients_identified=len(by_patient),
            reminders_sent=metrics["sent"],
            reminders_failed=metrics["failed"],
            notes=(
                f"Sent {metrics['sent']}/{len(by_patient)} reminders via "
                f"{metrics['channel']}."
            ),
        )
        logger.info(
            "[%s] Complete – sent=%d  failed=%d",
            JOB_NAME,
            metrics["sent"],
            metrics["failed"],
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] Unhandled error – job aborted", JOB_NAME)
        try:
            _log_run(
                db,
                started_at=started_at,
                status="error",
                patients_identified=0,
                reminders_sent=0,
                reminders_failed=0,
                notes=str(exc),
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not write error to job_run_log")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Scheduler registration helpers
# ---------------------------------------------------------------------------

def schedule_refill_job(scheduler) -> None:  # type: ignore[type-arg]
    """
    Register the daily job with an APScheduler instance.

    Usage (in app startup):
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler(timezone="Africa/Lagos")
        schedule_refill_job(scheduler)
        scheduler.start()
    """
    from apscheduler.triggers.cron import CronTrigger  # noqa: PLC0415

    scheduler.add_job(
        run_daily_refill_job,
        trigger=CronTrigger(hour=9, minute=0, timezone="Africa/Lagos"),
        id=JOB_ID,
        name=JOB_NAME,
        replace_existing=True,
        misfire_grace_time=600,   # tolerate up to 10-min startup lag
    )
    logger.info("Refill scheduler registered: %s (09:00 WAT daily)", JOB_ID)


# Celery Beat schedule entry (add to settings.py / celery.py)
CELERY_BEAT_ENTRY = {
    JOB_ID: {
        "task": "backend.core.refill_scheduler.run_daily_refill_job",
        "schedule": {
            # crontab(hour=8, minute=0) in UTC == 09:00 WAT (UTC+1)
            "crontab": {"hour": "8", "minute": "0"},
        },
        "options": {"queue": "scheduled"},
    }
}


# ---------------------------------------------------------------------------
# Internal logging helper
# ---------------------------------------------------------------------------

def _log_run(
    db: Session,
    started_at: datetime,
    status: str,
    patients_identified: int,
    reminders_sent: int,
    reminders_failed: int,
    notes: str,
) -> None:
    from sqlalchemy import text  # noqa: PLC0415

    finished_at = datetime.now(timezone.utc)
    duration_secs = (finished_at - started_at).total_seconds()

    sql = text(
        """
        INSERT INTO job_run_log (
            job_id,
            started_at,
            finished_at,
            duration_secs,
            status,
            patients_identified,
            reminders_sent,
            reminders_failed,
            notes
        ) VALUES (
            :job_id,
            :started_at,
            :finished_at,
            :duration_secs,
            :status,
            :patients_identified,
            :reminders_sent,
            :reminders_failed,
            :notes
        )
        """
    )
    db.execute(
        sql,
        {
            "job_id": JOB_ID,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_secs": round(duration_secs, 2),
            "status": status,
            "patients_identified": patients_identified,
            "reminders_sent": reminders_sent,
            "reminders_failed": reminders_failed,
            "notes": notes,
        },
    )
    db.commit()
