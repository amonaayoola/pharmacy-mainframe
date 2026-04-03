# Pharmacy Intelligence Mainframe
**Autonomous nervous system for Nigerian retail pharmacies**

Solves: counterfeit drugs, inflation-driven margin erosion, and patient churn.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    PHARMACY MAINFRAME                           │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │  FastAPI     │   │ PostgreSQL   │   │  Background      │   │
│  │  Backend     │◄──│  Database    │   │  Scheduler       │   │
│  │  :8000       │   │  :5432       │   │  (APScheduler)   │   │
│  └──────┬───────┘   └──────────────┘   └──────────────────┘   │
│         │                                                       │
│  ┌──────▼───────────────────────────────────────────────────┐  │
│  │              SERVICE LAYER                               │  │
│  │  ClinicalGateway │ PricingEngine │ NAFDACService │ WA   │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                                      │
          ▼                                      ▼
   ┌─────────────┐                      ┌───────────────┐
   │  React      │                      │  WhatsApp     │
   │  Frontend   │                      │  Bot          │
   │  (Vite)     │                      │  (Meta API)   │
   └─────────────┘                      └───────────────┘
```

## Stack

| Layer | Technology |
|-------|-----------|
| API | FastAPI 0.111 + Python 3.12 |
| Database | PostgreSQL 16 + SQLAlchemy ORM |
| Migrations | Alembic |
| Caching | Redis 7 |
| Scheduling | APScheduler 3.10 |
| WhatsApp | Meta Cloud API / Twilio |
| FX Rates | AbokiFX API |
| Drug Auth | NAFDAC API |
| Deployment | Docker Compose + Nginx |

---

## Quick Start (Development)

### 1. Prerequisites
```bash
# Install: Python 3.12+, PostgreSQL 16, Redis (optional), Docker
python --version   # 3.12+
psql --version     # 16+
```

### 2. Clone & Configure
```bash
git clone https://github.com/your-org/pharmacy-mainframe.git
cd pharmacy-mainframe

# Configure environment
cp .env.example .env
# Edit .env with your database credentials, WhatsApp tokens, etc.
nano .env
```

### 3. Database Setup
```bash
# Create PostgreSQL database
psql -U postgres -c "CREATE USER pharmacy_user WITH PASSWORD 'pharmacy_pass';"
psql -U postgres -c "CREATE DATABASE pharmacy_mainframe OWNER pharmacy_user;"
psql -U postgres -c "CREATE EXTENSION pg_trgm;" -d pharmacy_mainframe
```

### 4. Install & Run API
```bash
cd backend
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Seed with sample data (patients, drugs, wholesalers)
python -m scripts.seed_db

# Start the API server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. Verify It's Running
```
API Docs:    http://localhost:8000/api/docs
Health:      http://localhost:8000/api/health
Dashboard:   http://localhost:8000/api/dashboard/summary
```

---

## Docker Deployment (Production)

```bash
# Configure production environment
cp .env.example .env
nano .env  # Set real DB passwords, API keys, domain name

# Build and start all services
docker-compose up -d

# Check status
docker-compose ps
docker-compose logs -f api

# Seed initial data
docker-compose exec api python -m scripts.seed_db
```

---

## API Reference

### Core Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | System health check |
| GET | `/api/dashboard/summary` | Live KPI dashboard |
| GET | `/api/dashboard/revenue-chart` | Daily revenue (last 7 days) |
| GET | `/api/drugs/` | Drug registry (with search) |
| POST | `/api/drugs/` | Register new drug |
| POST | `/api/dispense/` | Complete a dispensing transaction |
| POST | `/api/dispense/audit` | Pre-flight clinical audit |
| GET | `/api/patients/` | Patient world model |
| POST | `/api/patients/` | Register new patient |
| GET | `/api/patients/due-refills` | Patients due for refill |
| GET | `/api/inventory/` | Full stock registry |
| GET | `/api/inventory/expiring` | Items expiring < 90 days |
| POST | `/api/inventory/batches` | Receive new stock batch |
| POST | `/api/pricing/calculate` | Dynamic price calculation |
| GET | `/api/pricing/fx-rate` | Live NGN/USD rate |
| GET | `/api/nafdac/verify/{batch_no}` | Authenticate drug batch |
| POST | `/api/procurement/` | Create purchase order |
| PATCH | `/api/procurement/{id}/approve` | Approve & send PO |
| POST | `/api/whatsapp/send` | Send WhatsApp message |
| POST | `/api/whatsapp/webhook` | Receive inbound WhatsApp |

### Example: Dispense with Clinical Audit
```bash
curl -X POST http://localhost:8000/api/dispense/ \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {"drug_id": 1, "quantity": 1},
      {"drug_id": 6, "quantity": 2}
    ],
    "patient_id": 1,
    "payment_method": "pos",
    "served_by": "Pharm. Adaora"
  }'

# If ACT + Vit C:
# {"audit_result": "BLOCK", "safe_to_dispense": false, ...}
```

### Example: Dynamic Pricing
```bash
curl -X POST http://localhost:8000/api/pricing/calculate \
  -H "Content-Type: application/json" \
  -d '{"cost_usd": 2.80, "margin": 0.25}'

# Response:
# {"cost_usd": 2.80, "fx_rate": 1578.0, "landed_ngn": 4418.4, "retail_ngn": 5890.0}
```

### Example: NAFDAC Verify
```bash
curl http://localhost:8000/api/nafdac/verify/GS-2024-1192
# {"status": "verified", "safe_to_dispense": true, "drug_name": "Paracetamol 500mg", ...}

curl http://localhost:8000/api/nafdac/verify/FAKE-BATCH
# {"status": "pending", "safe_to_dispense": false, "message": "DO NOT DISPENSE..."}
```

---

## Background Jobs

The scheduler runs 4 automatic jobs on the Africa/Lagos timezone:

| Job | Schedule | What It Does |
|-----|----------|--------------|
| FX Rate Sync | Every 6 hours | Fetches live NGN/USD from AbokiFX, reprices inventory |
| Refill Outreach | Daily 09:00 WAT | WhatsApps patients with refills due in 3 days |
| Expiry Watchdog | Daily 06:00 WAT | Flags items < 90 days to Promotion status |
| Auto-Procurement | Daily 07:00 WAT | Generates draft POs when stock < 7 days remaining |

---

## Clinical Drug Interaction Rules

The Clinical Gateway blocks or warns on these interactions:

| Combination | Level | Reason |
|------------|-------|--------|
| ACT + High-dose Vit C | BLOCK | Reduces artemisinin efficacy ~40% |
| SSRI + MAOI | BLOCK | Serotonin Syndrome — potentially fatal |
| Anticoagulant + NSAID | BLOCK | Severe bleeding risk |
| Antihypertensive + PDE5 inhibitor | BLOCK | Severe hypotension |
| Metformin + Contrast dye | BLOCK | Lactic acidosis risk |
| ACE Inhibitor + K-sparing diuretic | WARN | Hyperkalaemia monitoring |
| Quinolone + Antacids | WARN | 90% absorption reduction |
| Statin + Macrolide | WARN | Myopathy risk |

All rules are tag-based — new drugs automatically inherit rules when tagged correctly.

---

## Running Tests

```bash
cd backend
pytest tests/ -v                          # All tests
pytest tests/test_all.py::TestClinicalGateway -v  # Clinical tests only
pytest tests/ --cov=app --cov-report=html  # Coverage report
```

---

## WhatsApp Bot Setup

### Meta Cloud API (Recommended)
1. Create a Meta Developer App at [developers.facebook.com](https://developers.facebook.com)
2. Add WhatsApp Business product
3. Get Phone Number ID and Access Token
4. Set Webhook URL: `https://your-domain.com/api/whatsapp/webhook`
5. Set Verify Token: `mainframe_verify_token` (or change in whatsapp.py)

### Twilio (Alternative)
1. Create account at [twilio.com/whatsapp](https://www.twilio.com/whatsapp)
2. Set `WHATSAPP_PROVIDER=twilio` in `.env`
3. Fill in `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`

---

## Nigerian Factor — Offline Mode

For NEPA blackouts and internet outages, add **PouchDB** to the React frontend:

```javascript
// frontend/src/lib/offlineDB.js
import PouchDB from 'pouchdb';

const localDB = new PouchDB('pharmacy_offline');

// Sync with remote when online
const sync = PouchDB.sync(localDB, 'http://localhost:5984/pharmacy_mainframe', {
  live: true,
  retry: true,
}).on('change', info => console.log('Sync:', info))
  .on('error', err => console.warn('Offline — using local data'));

export { localDB, sync };
```

This ensures dispensing continues during outages, syncing to the mainframe when connectivity returns.

---

## Project Structure

```
pharmacy-mainframe/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI entry point
│   │   ├── core/
│   │   │   ├── config.py        # All settings / env vars
│   │   │   ├── database.py      # SQLAlchemy engine + session
│   │   │   └── scheduler.py     # APScheduler background jobs
│   │   ├── models/
│   │   │   └── models.py        # All database models (ORM)
│   │   ├── services/
│   │   │   ├── clinical_service.py   # Drug interaction engine
│   │   │   ├── fx_service.py         # Live FX + pricing engine
│   │   │   ├── nafdac_service.py     # Batch authentication
│   │   │   └── whatsapp_service.py   # WhatsApp messaging
│   │   └── api/
│   │       ├── dispensing.py    # POS + clinical audit endpoint
│   │       ├── drugs.py         # Drug registry CRUD
│   │       ├── patients.py      # Patient world model
│   │       ├── inventory.py     # Stock management
│   │       ├── procurement.py   # Purchase orders
│   │       ├── pricing.py       # Dynamic pricing + FX
│   │       ├── nafdac.py        # NAFDAC verification
│   │       ├── whatsapp.py      # WhatsApp webhook + send
│   │       └── dashboard.py     # KPI aggregates
│   ├── alembic/                 # Database migrations
│   ├── scripts/
│   │   └── seed_db.py           # Sample data seeder
│   ├── tests/
│   │   └── test_all.py          # Full test suite
│   └── requirements.txt
├── docker/
│   ├── Dockerfile.api
│   ├── nginx/nginx.conf
│   └── postgres/init.sql
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Next Steps

- [ ] React frontend integration (connect to `/api/*` endpoints)
- [ ] PouchDB offline sync for NEPA resilience
- [ ] SMS fallback for patients without WhatsApp
- [ ] Multi-branch support with row-level security
- [ ] NHIA claims integration (National Health Insurance)
- [ ] Barcode scanner integration (batch_no auto-fill)
- [ ] Reporting dashboard (weekly/monthly PDF reports)
