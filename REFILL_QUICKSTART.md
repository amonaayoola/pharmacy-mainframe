# Refill Management – Quick Start Guide

## Setup (5 minutes)

### 1. Database

Create the audit tables:

```sql
-- Table 1: Outreach log (tracks every WhatsApp/SMS sent)
CREATE TABLE IF NOT EXISTS outreach_log (
    id SERIAL PRIMARY KEY,
    patient_id INT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    drug_id INT NOT NULL REFERENCES drugs(id) ON DELETE CASCADE,
    channel VARCHAR(50) NOT NULL,                    -- 'whatsapp', 'sms', 'email'
    message_id VARCHAR(255),                         -- external provider message ID
    status VARCHAR(50) DEFAULT 'queued',             -- 'queued', 'sent', 'failed'
    error_msg TEXT,
    queued_at TIMESTAMP,
    sent_at TIMESTAMP,
    delivery_status VARCHAR(50),                     -- 'delivered', 'read', etc.
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_outreach_patient ON outreach_log(patient_id);
CREATE INDEX idx_outreach_status ON outreach_log(status);
CREATE INDEX idx_outreach_created ON outreach_log(created_at DESC);


-- Table 2: Job run log (audit trail for daily refill job)
CREATE TABLE IF NOT EXISTS job_run_log (
    id SERIAL PRIMARY KEY,
    job_name VARCHAR(255),
    status VARCHAR(50),                              -- 'success', 'partial', 'error'
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    patients_identified INT,
    messages_queued INT,
    messages_sent INT,
    messages_failed INT,
    error_details TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 2. FastAPI Router

Register the refill endpoints in your main `app.py`:

```python
from fastapi import FastAPI
from backend.api.refill_management import router as refill_router

app = FastAPI()

# Include all refill endpoints under /api/patients
app.include_router(refill_router, prefix="/api")
```

### 3. Scheduler

Set up APScheduler to run the daily job:

```python
from apscheduler.schedulers.background import BackgroundScheduler
from backend.core.refill_scheduler import schedule_refill_job

# In your app startup
scheduler = BackgroundScheduler()
schedule_refill_job(scheduler)
scheduler.start()
```

---

## API Endpoints

### 1. List All At-Risk Patients

**Get patients who need refills within the next 7 days.**

```bash
curl -X GET "http://localhost:8000/api/patients/due-for-refill?days_window=7"
```

**Response:**
```json
{
  "at_risk_count": 42,
  "patients": [
    {
      "patient_id": 101,
      "patient_name": "John Doe",
      "drug_id": 5,
      "drug_name": "Amlodipine",
      "drug_strength": "5mg",
      "adherence_rate": 0.85,
      "days_until_stockout": 3,
      "days_supply_remaining": 3.0,
      "urgency": "critical",
      "last_dispense_date": "2026-03-27",
      "predicted_stockout_date": "2026-04-06",
      "refill_due_date": "2026-04-03",
      "avg_daily_consumption": 1.0
    },
    {
      "patient_id": 102,
      "patient_name": "Jane Smith",
      "drug_id": 8,
      "drug_name": "Metformin",
      "drug_strength": "500mg",
      "adherence_rate": 0.92,
      "days_until_stockout": 5,
      "days_supply_remaining": 5.0,
      "urgency": "high",
      "last_dispense_date": "2026-03-29",
      "predicted_stockout_date": "2026-04-08",
      "refill_due_date": "2026-04-05",
      "avg_daily_consumption": 2.0
    }
  ]
}
```

**Urgency Levels:**
- `critical` – ≤2 days
- `high` – 3-4 days
- `medium` – 5-7 days

---

### 2. Get Patient Refill History

**90-day medication timeline for one patient.**

```bash
curl -X GET "http://localhost:8000/api/patients/101/refill-history"
```

**Response:**
```json
{
  "patient_id": 101,
  "patient_name": "John Doe",
  "medications": [
    {
      "drug_id": 5,
      "drug_name": "Amlodipine",
      "drug_strength": "5mg",
      "cycle_days": 30,
      "standard_qty": 30,
      "timeline": [
        {
          "date": "2026-03-27",
          "qty": 30,
          "type": "dispense",
          "days_since_last": null,
          "confidence": "recent"
        }
      ],
      "summary": {
        "adherence_rate": 0.85,
        "avg_daily_consumption": 1.0,
        "days_supply_remaining": 3.0,
        "predicted_stockout_date": "2026-04-06"
      }
    }
  ]
}
```

---

### 3. Send Manual Refill Reminder

**Trigger a WhatsApp reminder (override daily job).**

```bash
curl -X POST "http://localhost:8000/api/patients/101/send-refill-reminder" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "whatsapp",
    "custom_message": null
  }'
```

**Response:**
```json
{
  "success": true,
  "message_id": "msg_123456",
  "queued_at": "2026-04-04T10:30:00Z",
  "channel": "whatsapp"
}
```

---

### 4. Get Dashboard Summary

**Overall metrics for the pharmacy dashboard.**

```bash
curl -X GET "http://localhost:8000/api/patients/summary"
```

**Response:**
```json
{
  "total_patients_analysed": 250,
  "at_risk_count": 42,
  "due_today": 8,
  "due_in_3_days": 15,
  "due_in_7_days": 42,
  "generated_at": "2026-04-04T09:00:00Z"
}
```

---

## Database Queries

### Check What Ran Yesterday

```sql
SELECT * FROM job_run_log 
WHERE DATE(created_at) = CURRENT_DATE - INTERVAL 1 DAY;
```

**Result:**
```
| id | job_name | status | started_at | finished_at | patients_identified | messages_queued | messages_sent | messages_failed |
|----|----------|--------|------------|-------------|---------------------|-----------------|---------------|-----------------|
| 1  | Patient Refill Intelligence – Daily Run | success | 2026-04-03 09:00:00 | 2026-04-03 09:02:15 | 42 | 42 | 42 | 0 |
```

### All Outreach Attempts (Last 7 Days)

```sql
SELECT 
    ol.id,
    p.full_name,
    d.generic_name,
    ol.channel,
    ol.status,
    ol.created_at
FROM outreach_log ol
JOIN patients p ON ol.patient_id = p.id
JOIN drugs d ON ol.drug_id = d.id
WHERE ol.created_at >= NOW() - INTERVAL 7 DAY
ORDER BY ol.created_at DESC;
```

### Failed Messages (Troubleshooting)

```sql
SELECT * FROM outreach_log 
WHERE status = 'failed' 
  AND created_at >= NOW() - INTERVAL 1 DAY
ORDER BY created_at DESC;
```

---

## Python Usage

### Get At-Risk Patients Programmatically

```python
from sqlalchemy.orm import Session
from backend.services.refill_analytics import RefillAnalyticsEngine

db: Session = get_db()
analytics = RefillAnalyticsEngine(db)

# Get all at-risk patients
at_risk = analytics.get_at_risk_patients()

for profile in at_risk:
    print(f"{profile.drug_name} – {profile.days_until_stockout} days left")
    # Output: Amlodipine 5mg – 3 days left
```

### Send Custom Reminder

```python
from backend.services.refill_outreach import RefillOutreachService

outreach = RefillOutreachService(db)

# Manually send a reminder
result = outreach.queue_reminder(
    patient_id=101,
    profiles=at_risk,
    channel="whatsapp",
    custom_message="Hi John! Your Amlodipine is running low. Reply YES to refill."
)

print(result)
# {'success': True, 'message_id': 'msg_123456', 'queued_at': '2026-04-04T...'}
```

### Check Outreach History

```python
history = outreach.get_outreach_log(
    patient_id=101,
    drug_id=5,
    days=30
)

for log in history:
    print(f"{log['sent_at']} – {log['status']} – {log['channel']}")
```

---

## Troubleshooting

### Daily Job Not Running?

1. Check APScheduler is started:
   ```python
   print(scheduler.get_jobs())  # Should show "Patient Refill Intelligence – Daily Run"
   ```

2. Check logs at 09:00 WAT:
   ```sql
   SELECT * FROM job_run_log 
   WHERE DATE(created_at) = CURRENT_DATE
   ORDER BY created_at DESC;
   ```

3. Check for errors:
   ```sql
   SELECT error_details FROM job_run_log 
   WHERE status IN ('error', 'partial')
   ORDER BY created_at DESC;
   ```

### WhatsApp Messages Not Sending?

1. Check service is configured:
   ```python
   from backend.app.services.whatsapp_service import whatsapp_service
   print(whatsapp_service.configured)  # Should be True
   ```

2. Check queued status:
   ```sql
   SELECT COUNT(*) FROM outreach_log WHERE status = 'queued';
   ```

3. View error:
   ```sql
   SELECT error_msg FROM outreach_log 
   WHERE status = 'failed' 
   ORDER BY created_at DESC LIMIT 1;
   ```

### Patient Not Appearing in Due List?

1. Check patient has `whatsapp_opted_in = true`:
   ```sql
   SELECT id, full_name, whatsapp_opted_in FROM patients 
   WHERE id = 101;
   ```

2. Check refill schedule exists and is active:
   ```sql
   SELECT * FROM refill_schedules 
   WHERE patient_id = 101 AND is_active = true;
   ```

3. Check dispensing history (need at least 1 dispense):
   ```sql
   SELECT * FROM dispensing_records 
   WHERE patient_id = 101 
   ORDER BY created_at DESC LIMIT 5;
   ```

---

## Monitoring Checklist

- [ ] Job runs at 09:00 WAT daily
- [ ] `job_run_log.status` = 'success'
- [ ] Messages queued > 0
- [ ] Messages failed = 0
- [ ] WhatsApp delivery status tracked
- [ ] No database query errors in logs

---

## Next Steps

1. **Deploy migrations** – Run SQL above
2. **Test endpoints** – Use curl commands above
3. **Train pharmacy team** – Show them the `/due-for-refill` endpoint
4. **Monitor 24/7** – Set up alerts on `job_run_log.status != 'success'`
5. **Iterate** – Adjust lead times, message templates based on feedback

---

**Questions?** Check `PHASE_1D_SUMMARY.md` for detailed architecture.
