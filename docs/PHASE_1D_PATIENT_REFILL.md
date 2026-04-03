# PHASE 1D – Patient Refill Intelligence

**Status:** Ready for dev review  
**Lagos Time:** Daily job fires at 09:00 WAT (08:00 UTC)  
**Depends on:** Phase 1A (DB schema), Phase 1B (WhatsApp integration), Phase 1C (procurement engine)

---

## 1. Overview

Phase 1D adds proactive refill intelligence to the pharmacy platform.  
Instead of waiting for patients to return, the system predicts when each
patient will run out of medication and sends a WhatsApp reminder before that
happens — eliminating stockouts at the patient level.

```
dispensing_records
      │
      ▼
RefillAnalyticsEngine          ← runs daily at 09:00 WAT
  • adherence rate per patient/drug
  • avg daily consumption
  • predicted stockout date
      │
      ├─ at_risk (≤7 days)
      │       │
      │       ▼
      │  RefillOutreachService
      │    • render WhatsApp / SMS message
      │    • dispatch via existing gateway
      │    • write outreach_log
      │
      └─ summary metrics → job_run_log
```

---

## 2. File Map

| File | Role |
|---|---|
| `backend/services/refill_analytics.py` | Prediction engine – adherence + stockout math |
| `backend/api/refill_management.py` | FastAPI endpoints (3 routes) |
| `backend/services/refill_outreach.py` | Notification queue + dispatch |
| `backend/core/refill_scheduler.py` | APScheduler / Celery daily job |
| `backend/services/procurement_trigger.py` | Phase 1C bug fix (see §7) |

---

## 3. Analytics Engine

**File:** `backend/services/refill_analytics.py`

### Adherence Rate

```
adherence = covered_days / span_days

covered_days = Σ (quantity_dispensed_i / avg_daily)
span_days    = last_dispense_date − first_dispense_date  (min 1)
```

Capped at 1.0 (perfect adherence).  
Requires ≥ 2 dispenses; single-fill patients are flagged but not predicted.

### Average Daily Consumption

```
avg_daily = total_quantity / span_days
```

Computed over the last 90 days of dispensing history.

### Days of Supply Remaining

```
supply_at_last_fill = last_qty / avg_daily
days_since_fill     = today − last_dispense_date
days_remaining      = supply_at_last_fill − days_since_fill  (floor 0)
```

### Risk Window & Refill Lead Time

| Constant | Default | Purpose |
|---|---|---|
| `RISK_WINDOW_DAYS` | 7 | Flag patient if stockout ≤ N days away |
| `REFILL_LEAD_DAYS` | 3 | Suggest refill N days before stockout |
| `MIN_DISPENSES_FOR_PREDICTION` | 2 | Minimum fills needed to predict |

### Urgency Tiers (API)

| Tier | Days Until Stockout |
|---|---|
| `critical` | 0 – 2 |
| `high` | 3 – 4 |
| `medium` | 5 – 7 |

---

## 4. API Endpoints

**File:** `backend/api/refill_management.py`  
**Router prefix:** `/patients`

### GET `/patients/due-for-refill`

Returns all patients predicted to run out within 7 days, sorted by urgency.

**Query params:**

| Param | Type | Default | Description |
|---|---|---|---|
| `urgency_filter` | string | — | Filter to `critical`, `high`, or `medium` |

**Response** `200 OK` – array of `RefillPatientOut`:

```json
[
  {
    "patient_id": 42,
    "drug_id": 7,
    "drug_name": "Metformin 500mg",
    "avg_daily_consumption": 2.0,
    "adherence_rate": 0.91,
    "last_dispense_date": "2025-07-28",
    "days_supply_remaining": 4.5,
    "predicted_stockout_date": "2025-08-01",
    "days_until_stockout": 4,
    "refill_due_date": "2025-07-29",
    "urgency": "high"
  }
]
```

---

### GET `/patients/{patient_id}/refill-history`

90-day (configurable) dispense timeline with per-fill days-supply estimates.

**Query params:**

| Param | Default | Range |
|---|---|---|
| `window_days` | 90 | 7 – 365 |

**Response** `200 OK`:

```json
{
  "patient_id": 42,
  "window_days": 90,
  "overall_adherence": 0.88,
  "entries": [
    {
      "dispense_date": "2025-05-15",
      "drug_id": 7,
      "drug_name": "Metformin 500mg",
      "quantity_dispensed": 60,
      "days_supply": 30.0,
      "adherence_rate_at_fill": null
    }
  ]
}
```

---

### POST `/patients/{patient_id}/send-refill-reminder`

Immediately queues a refill reminder for a specific patient.

**Request body:**

```json
{
  "channel": "whatsapp",
  "override_message": null
}
```

**Response** `202 Accepted`:

```json
{
  "success": true,
  "message_id": "550e8400-e29b-41d4-a716-446655440000",
  "channel": "whatsapp",
  "queued_at": "2025-07-31T08:01:23+00:00"
}
```

---

## 5. Notification Service

**File:** `backend/services/refill_outreach.py`

### WhatsApp Template

```
Hello {patient_name} 👋

This is a friendly reminder from your pharmacy.

📋 *Medication Due for Refill:*
  • Metformin 500mg – 4 day(s) remaining
  • Lisinopril 10mg – 2 day(s) remaining

Please visit us or reply *REFILL* to place your order.

_Your health is our priority._ 🏥
```

### Delivery Lifecycle

```
queued → sent → [delivered] → [read]
                    ↑
              gateway webhook updates outreach_log.read_at
```

### Opt-out Fallback

If a patient's `whatsapp_opted_in = false`, the service automatically
falls back to SMS without error.

---

## 6. Daily Scheduler

**File:** `backend/core/refill_scheduler.py`

### Schedule

| Scheduler | Config |
|---|---|
| APScheduler | `CronTrigger(hour=9, minute=0, timezone="Africa/Lagos")` |
| Celery Beat | `hour="8", minute="0"` (UTC) |

### Run Steps

1. Open DB session
2. `RefillAnalyticsEngine.get_at_risk_patients()` → list of `AdherenceProfile`
3. Group profiles by `patient_id`
4. `RefillOutreachService.bulk_queue(by_patient)` → aggregated metrics
5. Write to `job_run_log`
6. Close DB session

### job_run_log Schema

```sql
CREATE TABLE job_run_log (
    id                  SERIAL PRIMARY KEY,
    job_id              TEXT NOT NULL,
    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ,
    duration_secs       NUMERIC(8,2),
    status              TEXT,          -- 'success' | 'error'
    patients_identified INT DEFAULT 0,
    reminders_sent      INT DEFAULT 0,
    reminders_failed    INT DEFAULT 0,
    notes               TEXT
);
```

---

## 7. Phase 1C Bug Fix – procurement_trigger.py

**Problem:** `_calculate_order_quantity()` contained two bugs:

1. `session.execute(query, ...)` referenced an undefined variable `query`
   instead of the actual `velocity_query`.
2. The second SQL statement (inventory stock lookup) reused the variable
   name `velocity_query`, so it silently overwrote the first query object
   and executed the wrong SQL — returning dispensing data instead of
   inventory movement data.

**Fix applied:**

```python
# BEFORE (broken)
velocity_query = text("SELECT ... FROM dispensing_records ...")
velocity_query = text("SELECT ... FROM inventory_movements ...")   # ← overwrites!
quantity = session.execute(query, {"drug_id": drug_id}).scalar() or 0  # ← NameError

# AFTER (fixed)
velocity_query = text("SELECT ... FROM dispensing_records ...")
velocity_result = self.db.execute(velocity_query, {"drug_id": drug_id}).first()
avg_daily_velocity = velocity_result[0] if velocity_result else 0.0

movement_query = text("SELECT ... FROM inventory_movements ...")   # distinct name
stock_result = self.db.execute(movement_query, {"drug_id": drug_id}).first()
current_stock = stock_result[0] if stock_result else 0
```

---

## 8. Required DB Tables

### New (Phase 1D)

```sql
-- Outreach delivery tracking
CREATE TABLE outreach_log (
    log_id             UUID PRIMARY KEY,
    patient_id         INT  NOT NULL REFERENCES patients(id),
    channel            TEXT NOT NULL,          -- 'whatsapp' | 'sms'
    message_body       TEXT NOT NULL,
    status             TEXT NOT NULL,          -- queued | sent | failed
    gateway_message_id TEXT,
    queued_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at            TIMESTAMPTZ,
    read_at            TIMESTAMPTZ,
    error_detail       TEXT
);

CREATE INDEX idx_outreach_patient ON outreach_log(patient_id);
CREATE INDEX idx_outreach_queued  ON outreach_log(queued_at DESC);
```

### Assumed Existing (Phase 1A)

```sql
dispensing_records (patient_id, drug_id, quantity_dispensed, dispensed_at)
inventory_movements (drug_id, movement_type, quantity)
patients (id, first_name, last_name, phone, whatsapp_opted_in, active)
drugs (id, name)
job_run_log (see §6)
```

---

## 9. Success Criteria Checklist

| Criterion | Implemented |
|---|---|
| Refill predictions run daily without errors | ✅ Scheduler + try/except + job_run_log |
| Patients identified 7 days before stockout | ✅ `RISK_WINDOW_DAYS = 7` |
| WhatsApp reminders sent automatically | ✅ `bulk_queue` in scheduler |
| Adherence patterns visible for pharmacist review | ✅ `/refill-history` + `adherence_rate` on every response |
| No missed refills | ✅ 3-day lead-time buffer (`REFILL_LEAD_DAYS = 3`) |
| Delivery tracking | ✅ `outreach_log` table + `/delivery-report` method |

---

## 10. Integration Checklist (Dev Handoff)

- [ ] Add `router` from `refill_management.py` to `main.py` (`app.include_router(router)`)
- [ ] Call `schedule_refill_job(scheduler)` in app startup (APScheduler) **or** add `CELERY_BEAT_ENTRY` to Celery config
- [ ] Run migration to create `outreach_log` table (see §8)
- [ ] Verify `backend.integrations.whatsapp_client.WhatsAppClient.send_message(to, body)` signature matches usage in `refill_outreach.py`
- [ ] Verify `backend.integrations.sms_client.SMSClient.send(to, body)` signature
- [ ] Deploy updated `procurement_trigger.py` (Phase 1C bug fix)
- [ ] Smoke-test: `GET /patients/due-for-refill` returns valid JSON with test data
