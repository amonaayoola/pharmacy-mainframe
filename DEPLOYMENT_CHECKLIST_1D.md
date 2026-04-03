# Phase 1D Deployment Checklist

**Phase:** 1D – Patient Refill Intelligence  
**Status:** ✅ Code Complete  
**Target Date:** Next available deployment window  

---

## Pre-Deployment (Dev/Staging)

- [ ] **Code Review**
  - [ ] `refill_analytics.py` – Consumption calculation logic approved
  - [ ] `refill_outreach.py` – WhatsApp integration tested
  - [ ] `refill_scheduler.py` – Job timing verified (09:00 WAT)
  - [ ] All tests passing: `pytest backend/tests/test_refill_*.py -v`

- [ ] **Database Schema**
  - [ ] Run migrations to create `outreach_log` table
  - [ ] Run migrations to create `job_run_log` table
  - [ ] Verify indexes created:
    ```sql
    SELECT * FROM pg_indexes WHERE tablename IN ('outreach_log', 'job_run_log');
    ```
  - [ ] Test patient data has `whatsapp_opted_in` field
  - [ ] Test dispensing records exist for sample patients

- [ ] **API Endpoints**
  - [ ] Test `GET /api/patients/due-for-refill` returns data
  - [ ] Test `GET /api/patients/{id}/refill-history` for sample patient
  - [ ] Test `POST /api/patients/{id}/send-refill-reminder` manually triggers job
  - [ ] Test `GET /api/patients/summary` returns metrics
  - [ ] Response times acceptable (<500ms)

- [ ] **Scheduler Setup**
  - [ ] APScheduler installed and configured
  - [ ] Job registered with correct trigger (09:00 WAT)
  - [ ] Job can be started/stopped cleanly
  - [ ] Logs written to `job_run_log` on each run

- [ ] **WhatsApp Integration**
  - [ ] WhatsApp service configured with valid credentials
  - [ ] Test message delivery succeeds
  - [ ] Graceful fallback when service unavailable
  - [ ] Message templates reviewed by pharmacy team

- [ ] **Documentation Reviewed**
  - [ ] `PHASE_1D_SUMMARY.md` reviewed
  - [ ] `REFILL_QUICKSTART.md` tested with real data
  - [ ] Team understands at-risk flagging rules (≤7 days)
  - [ ] Pharmacy staff trained on new endpoints

---

## Staging Deployment

- [ ] **Database**
  - [ ] Migrations applied to staging
  - [ ] Backup created before applying migrations
  - [ ] Sample patient data loaded (≥100 patients with refill schedules)
  - [ ] Verify tables and indexes:
    ```sql
    \d outreach_log
    \d job_run_log
    ```

- [ ] **Application**
  - [ ] FastAPI router included in staging app
  - [ ] Scheduler registered and running
  - [ ] WhatsApp service configured (test credentials)
  - [ ] Health check passes: `GET /api/health` → 200

- [ ] **Integration Testing**
  - [ ] Run 24-hour test cycle (full daily job)
  - [ ] Verify at-risk patients identified correctly
  - [ ] Verify WhatsApp messages queued and sent
  - [ ] Check `job_run_log` has success entries
  - [ ] Check `outreach_log` shows message delivery status

- [ ] **Monitoring & Alerts**
  - [ ] DataDog/Prometheus metrics configured
  - [ ] Alert set: `job_run_log.status != 'success'`
  - [ ] Alert set: `outreach_log.status = 'failed'` in last 1h
  - [ ] Dashboard created: job runs, message volume, failure rate

- [ ] **Load Testing**
  - [ ] `/patients/due-for-refill` tested with 1000+ patients
  - [ ] Job performance acceptable with full data set
  - [ ] Memory usage stable during job runs
  - [ ] No database connection leaks

---

## Production Deployment

- [ ] **Pre-Production**
  - [ ] Code freeze confirmed (no new commits to main)
  - [ ] Release notes prepared:
    - New tables created
    - Daily job runs 09:00 WAT
    - New API endpoints available
    - WhatsApp reminders automatic
  - [ ] Runbook created for incident response

- [ ] **Backup & Rollback**
  - [ ] Production database backed up
  - [ ] Rollback plan documented (reverse migrations)
  - [ ] Previous version deployed on standby
  - [ ] Rollback tested on non-prod (full procedure)

- [ ] **Deploy**
  - [ ] Schedule deployment window (off-peak hours)
  - [ ] Notify pharmacy team of deployment
  - [ ] Apply database migrations
  - [ ] Deploy code to production
  - [ ] Verify FastAPI routes registered
  - [ ] Health check passes: `GET /health` → 200

- [ ] **Post-Deployment Verification**
  - [ ] First job runs at 09:00 WAT (or manual trigger)
  - [ ] `job_run_log` shows successful run
  - [ ] `outreach_log` shows messages queued/sent
  - [ ] API endpoints respond correctly
  - [ ] No errors in application logs
  - [ ] Database performance within SLA

- [ ] **Pharmacy Team Handoff**
  - [ ] Team trained on new `/due-for-refill` endpoint
  - [ ] Team understands urgency levels (critical/high/medium)
  - [ ] Pharmacist can manually trigger reminders
  - [ ] Pharmacist can view outreach history for patients
  - [ ] Support escalation process defined

- [ ] **Monitoring Live**
  - [ ] Watch dashboard for first 24 hours
  - [ ] Job runs at 09:00 WAT ✓
  - [ ] No unexpected errors in logs
  - [ ] Pharmacy staff report WhatsApp messages received
  - [ ] Database queries performant
  - [ ] No database connection exhaustion

---

## Post-Deployment (Week 1)

- [ ] **Operational Verification**
  - [ ] Daily job runs successfully all 7 days
  - [ ] No failed deployments or rollbacks
  - [ ] Patient feedback positive (reminders received)
  - [ ] Pharmacist dashboard usage normal
  - [ ] Database size growth acceptable

- [ ] **Performance Review**
  - [ ] Query performance meets SLA (<500ms)
  - [ ] Job completion time <5 minutes
  - [ ] WhatsApp delivery rate >95%
  - [ ] Error rate <1%

- [ ] **Bug Tracking**
  - [ ] Create issue for any edge cases found
  - [ ] Track message delivery failures by reason
  - [ ] Monitor for off-by-one errors in date calculations
  - [ ] Collect feedback from pharmacy team

---

## Success Criteria (Final Sign-Off)

- ✅ **At-risk Detection:** Patients with ≤7 days supply correctly identified
- ✅ **Message Delivery:** WhatsApp reminders sent to opted-in patients
- ✅ **Audit Trail:** All outreach logged in `outreach_log` table
- ✅ **Job Reliability:** Daily 09:00 WAT job runs error-free
- ✅ **API Availability:** All endpoints responsive <500ms
- ✅ **Team Capability:** Pharmacy can manage refills via dashboard

**Sign-Off Approvals:**
- [ ] Engineering Lead
- [ ] Pharmacy Director
- [ ] DevOps/Infrastructure
- [ ] QA Lead

---

## Rollback Plan (If Needed)

**Trigger:** Job failures, WhatsApp not sending, API errors

**Steps:**
1. Revert code to previous commit:
   ```bash
   git revert <commit_hash>
   git push origin main
   ```

2. Reverse database migrations:
   ```bash
   alembic downgrade -1
   ```

3. Restart application:
   ```bash
   systemctl restart pharma-api
   ```

4. Verify rollback:
   ```bash
   curl http://localhost/api/health
   ```

5. Post-mortem:
   - Identify root cause
   - Fix in code
   - Re-test before next deployment

---

## Contacts & Escalation

| Role | Name | Contact | Availability |
|------|------|---------|--------------|
| Engineering Lead | Noah Divine | noah@example.com | 24/7 |
| Pharmacy Director | [Name] | [Contact] | 09:00-17:00 WAT |
| Database Admin | [Name] | [Contact] | On-call |
| WhatsApp Support | [Vendor] | [Contact] | [Hours] |

---

## Questions Before Deploy?

1. **Where will daily job run?** – APScheduler on app server (or separate cron box)
2. **What if WhatsApp is down?** – Messages queue, retry on next cycle
3. **How to test manually?** – `POST /api/patients/{id}/send-refill-reminder`
4. **How to monitor?** – Check `job_run_log` and `outreach_log` tables
5. **Can we adjust times?** – Yes, modify `REFILL_LEAD_DAYS` and `RISK_WINDOW_DAYS` in code

---

**Status:** Ready for deployment ✅

**Next Phase:** Monitoring, feedback collection, Phase 2 planning
