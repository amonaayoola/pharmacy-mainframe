"""
Pharmacy Intelligence Mainframe — FastAPI Backend
HealthBridge Lagos | Production API
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import time

from app.api import (
    drugs, patients, inventory, procurement,
    pricing, nafdac, dispensing, whatsapp, dashboard, pos, vendors,
    portal, portal_auth,
)
from app.core.config import settings
from app.core.database import engine, Base
from app.core.scheduler import start_scheduler
# Ensure all extended models are registered with SQLAlchemy metadata
import app.models.procurement_models  # noqa: F401
import app.models.portal_models       # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Pharmacy Intelligence Mainframe",
    description="Autonomous nervous system for Nigerian retail pharmacies",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    response.headers["X-Process-Time"] = str(round(time.time() - start, 4))
    return response

# Register routers
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(drugs.router, prefix="/api/drugs", tags=["Drugs"])
app.include_router(patients.router, prefix="/api/patients", tags=["Patients"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["Inventory"])
app.include_router(procurement.router, prefix="/api/procurement", tags=["Procurement"])
app.include_router(pricing.router, prefix="/api/pricing", tags=["Pricing"])
app.include_router(nafdac.router, prefix="/api/nafdac", tags=["NAFDAC"])
app.include_router(dispensing.router, prefix="/api/dispense", tags=["Dispensing"])
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp"])
app.include_router(pos.router, prefix="/api/pos", tags=["POS"])
app.include_router(vendors.router, prefix="/api/vendors", tags=["Vendors"])
app.include_router(portal_auth.router, prefix="/api/portal/auth", tags=["Patient Portal Auth"])
app.include_router(portal.router, prefix="/api/portal", tags=["Patient Portal"])

@app.on_event("startup")
async def startup_event():
    logger.info("🚀 Pharmacy Mainframe starting up...")
    start_scheduler()
    logger.info("✅ Background scheduler started")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Pharmacy Mainframe shutting down...")

@app.get("/api/health")
async def health_check():
    return {
        "status": "operational",
        "service": "Pharmacy Intelligence Mainframe",
        "branch": "HealthBridge Lagos — Branch 001",
        "version": "1.0.0"
    }

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Mainframe alert logged."}
    )
