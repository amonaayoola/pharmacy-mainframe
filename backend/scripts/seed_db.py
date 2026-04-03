"""
Database Seed Script — Pharmacy Intelligence Mainframe
Populates the database with realistic Nigerian pharmacy data for development.

Usage:
    cd backend
    python -m scripts.seed_db
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta
from app.core.database import SessionLocal, engine, Base
from app.models.models import (
    Drug, StockBatch, Patient, RefillSchedule, Wholesaler,
    FXRate, NAFDACStatus, StockStatus
)

def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        print("🌱 Seeding Pharmacy Mainframe database...")

        # ─── FX Rate ───────────────────────────────────────────────────────
        db.add(FXRate(usd_ngn=1578.00, source="AbokiFX"))
        db.flush()
        print("  ✅ FX rate seeded")

        # ─── Wholesalers ───────────────────────────────────────────────────
        wholesalers = [
            Wholesaler(name="Emzor Pharmaceutical Industries", contact_person="Mr. Emeka Osei",
                       phone="+234 1 555 0100", email="orders@emzor.com",
                       address="34 Oba Akran Avenue, Ikeja, Lagos", rating=5.0, lead_time_days=2),
            Wholesaler(name="Fidson Healthcare Plc", contact_person="Mrs. Tolu Adeyemi",
                       phone="+234 1 555 0200", email="supply@fidson.com",
                       address="61 Lagos-Abeokuta Expressway, Oshodi, Lagos", rating=4.5, lead_time_days=3),
            Wholesaler(name="May & Baker Nigeria Plc", contact_person="Dr. Chidi Obi",
                       phone="+234 1 555 0300", email="trade@mayandbaker.com",
                       address="3-5 Sapara Street, Ikeja, Lagos", rating=5.0, lead_time_days=2),
            Wholesaler(name="GlaxoSmithKline Nigeria Plc", contact_person="Ms. Ngozi Eze",
                       phone="+234 1 555 0400", email="nigeria@gsk.com",
                       address="Industrial Estate, Ilupeju, Lagos", rating=4.5, lead_time_days=5),
        ]
        for w in wholesalers:
            db.add(w)
        db.flush()
        print(f"  ✅ {len(wholesalers)} wholesalers seeded")

        # ─── Drugs ─────────────────────────────────────────────────────────
        drugs_data = [
            dict(generic_name="Artemether/Lumefantrine", brand_name="Coartem", strength="80mg/480mg",
                 dosage_form="Tablet", nafdac_reg_no="A7-0023-2021", manufacturer="Novartis AG",
                 drug_class="Antimalarial", tags=["ACT", "antimalarial", "prescription"],
                 requires_prescription=True, cost_usd=2.80,
                 clinical_flags={"conflict_with_tags": ["VIT_C_HIGH"], "msg": "ACT efficacy reduced by high-dose Vit C"}),
            dict(generic_name="Paracetamol", brand_name="Emzor Paracetamol", strength="500mg",
                 dosage_form="Tablet", nafdac_reg_no="A5-1192-2020", manufacturer="May & Baker Nigeria",
                 drug_class="Analgesic/Antipyretic", tags=["analgesic", "antipyretic", "OTC"],
                 cost_usd=0.12),
            dict(generic_name="Amoxicillin Trihydrate", brand_name="Ranbaxy Amoxicillin", strength="500mg",
                 dosage_form="Capsule", nafdac_reg_no="A1-3311-2022", manufacturer="Emzor Pharmaceuticals",
                 drug_class="Antibiotic", tags=["antibiotic", "penicillin", "prescription"],
                 requires_prescription=True, cost_usd=0.45),
            dict(generic_name="Metformin Hydrochloride", brand_name="Glucophage", strength="500mg",
                 dosage_form="Tablet", nafdac_reg_no="A3-8812-2021", manufacturer="Fidson Healthcare",
                 drug_class="Antidiabetic", tags=["antidiabetic", "metformin", "chronic", "prescription"],
                 requires_prescription=True, cost_usd=0.28),
            dict(generic_name="Amlodipine Besilate", brand_name="Norvasc", strength="10mg",
                 dosage_form="Tablet", nafdac_reg_no="A2-4421-2023", manufacturer="Pfizer Inc",
                 drug_class="Antihypertensive", tags=["antihypertensive", "CCB", "chronic", "prescription"],
                 requires_prescription=True, cost_usd=0.35),
            dict(generic_name="Ascorbic Acid", brand_name="Pharmaton Vitamin C", strength="1000mg",
                 dosage_form="Effervescent Tablet", nafdac_reg_no="S3-9921-2022", manufacturer="Pharmanord",
                 drug_class="Supplement", tags=["supplement", "VIT_C_HIGH", "OTC"],
                 cost_usd=0.22),
            dict(generic_name="Oral Rehydration Salts", brand_name="WHO-ORS", strength="20.5g/sachet",
                 dosage_form="Powder", nafdac_reg_no="A9-1100-2020", manufacturer="May & Baker Nigeria",
                 drug_class="Electrolyte", tags=["electrolyte", "ORS", "OTC"],
                 cost_usd=0.08),
            dict(generic_name="Ciprofloxacin Hydrochloride", brand_name="Ciprobay", strength="500mg",
                 dosage_form="Tablet", nafdac_reg_no="A4-5501-2021", manufacturer="Bayer AG",
                 drug_class="Antibiotic", tags=["antibiotic", "quinolone", "prescription"],
                 requires_prescription=True, cost_usd=0.55),
            dict(generic_name="Lisinopril", brand_name="Prinivil", strength="10mg",
                 dosage_form="Tablet", nafdac_reg_no="A6-7712-2022", manufacturer="Merck",
                 drug_class="Antihypertensive", tags=["antihypertensive", "ACE_inhibitor", "chronic", "prescription"],
                 requires_prescription=True, cost_usd=0.30),
            dict(generic_name="Atorvastatin Calcium", brand_name="Lipitor", strength="40mg",
                 dosage_form="Tablet", nafdac_reg_no="A8-3321-2023", manufacturer="Pfizer Inc",
                 drug_class="Lipid-lowering", tags=["statin", "lipid_lowering", "chronic", "prescription"],
                 requires_prescription=True, cost_usd=0.65),
        ]
        drug_objects = []
        for d in drugs_data:
            drug = Drug(**d)
            db.add(drug)
            drug_objects.append(drug)
        db.flush()
        print(f"  ✅ {len(drug_objects)} drugs seeded")

        # ─── Stock Batches ─────────────────────────────────────────────────
        batches = [
            (drug_objects[0], "GS-2024-0891", 4, date(2025, 12, 15), NAFDACStatus.verified),
            (drug_objects[1], "GS-2024-1192", 240, date(2026, 8, 20), NAFDACStatus.verified),
            (drug_objects[2], "RX-2024-3311", 88, date(2026, 5, 10), NAFDACStatus.verified),
            (drug_objects[3], "GF-2024-8812", 12, date(2026, 11, 30), NAFDACStatus.verified),
            (drug_objects[4], "NV-2024-4421", 18, date(2026, 2, 28), NAFDACStatus.verified),
            (drug_objects[5], "PT-2024-9921", 55, date(2025, 9, 15), NAFDACStatus.flagged),
            (drug_objects[6], "OR-2024-1100", 300, date(2027, 1, 1), NAFDACStatus.verified),
            (drug_objects[7], "CB-2024-5501", 62, date(2026, 7, 15), NAFDACStatus.verified),
            (drug_objects[8], "PR-2024-7712", 44, date(2026, 9, 30), NAFDACStatus.verified),
            (drug_objects[9], "LT-2024-3321", 38, date(2027, 3, 1), NAFDACStatus.verified),
        ]
        for drug, batch_no, qty, expiry, nafdac_status in batches:
            status = StockStatus.critical if qty < 10 else StockStatus.low if qty < 20 else StockStatus.ok
            if (expiry - date.today()).days < 90:
                status = StockStatus.promotion
            db.add(StockBatch(
                drug_id=drug.id, batch_no=batch_no, quantity=qty,
                unit_cost_usd=drug.cost_usd, expiry_date=expiry,
                nafdac_status=nafdac_status, status=status,
            ))
        db.flush()
        print(f"  ✅ {len(batches)} stock batches seeded")

        # ─── Patients ──────────────────────────────────────────────────────
        patients_data = [
            dict(full_name="Mrs. Adebayo Funmilayo", phone_number="+2348021112222",
                 date_of_birth=date(1965, 3, 15), gender="Female", address="12 Broad Street, Lagos Island",
                 condition_tags=["Hypertension"], whatsapp_opted_in=True),
            dict(full_name="Mr. Chukwuemeka Obi", phone_number="+2348033334444",
                 date_of_birth=date(1958, 7, 22), gender="Male", address="5 Awolowo Road, Ikoyi",
                 condition_tags=["Diabetes", "Hypertension"], whatsapp_opted_in=True),
            dict(full_name="Miss Blessing Eze", phone_number="+2348055556666",
                 date_of_birth=date(1995, 11, 8), gender="Female", address="Surulere, Lagos",
                 condition_tags=["Malaria"], whatsapp_opted_in=True),
            dict(full_name="Dr. Tunde Afolabi", phone_number="+2348067778888",
                 date_of_birth=date(1970, 5, 30), gender="Male", address="Victoria Island, Lagos",
                 condition_tags=["Hypertension"], whatsapp_opted_in=True),
            dict(full_name="Mrs. Ngozi Williams", phone_number="+2348089990000",
                 date_of_birth=date(1962, 9, 14), gender="Female", address="Lekki Phase 1, Lagos",
                 condition_tags=["Diabetes"], whatsapp_opted_in=True),
        ]
        patient_objects = []
        for p in patients_data:
            patient = Patient(**p)
            db.add(patient)
            patient_objects.append(patient)
        db.flush()
        print(f"  ✅ {len(patient_objects)} patients seeded")

        # ─── Refill Schedules ──────────────────────────────────────────────
        # Patient 1: Amlodipine — due in 2 days
        db.add(RefillSchedule(patient_id=patient_objects[0].id, drug_id=drug_objects[4].id,
                               cycle_days=30, standard_qty=30,
                               last_refill_date=date.today() - timedelta(days=28),
                               next_refill_date=date.today() + timedelta(days=2)))
        # Patient 2: Metformin — due in 3 days
        db.add(RefillSchedule(patient_id=patient_objects[1].id, drug_id=drug_objects[3].id,
                               cycle_days=30, standard_qty=30,
                               last_refill_date=date.today() - timedelta(days=27),
                               next_refill_date=date.today() + timedelta(days=3)))
        # Patient 2: Amlodipine also
        db.add(RefillSchedule(patient_id=patient_objects[1].id, drug_id=drug_objects[4].id,
                               cycle_days=30, standard_qty=30,
                               last_refill_date=date.today() - timedelta(days=25),
                               next_refill_date=date.today() + timedelta(days=5)))
        # Patient 4: Amlodipine — due in 7 days
        db.add(RefillSchedule(patient_id=patient_objects[3].id, drug_id=drug_objects[4].id,
                               cycle_days=30, standard_qty=30,
                               last_refill_date=date.today() - timedelta(days=23),
                               next_refill_date=date.today() + timedelta(days=7)))
        # Patient 5: Metformin — due in 12 days
        db.add(RefillSchedule(patient_id=patient_objects[4].id, drug_id=drug_objects[3].id,
                               cycle_days=30, standard_qty=30,
                               last_refill_date=date.today() - timedelta(days=18),
                               next_refill_date=date.today() + timedelta(days=12)))
        db.flush()
        print("  ✅ 5 refill schedules seeded")

        db.commit()
        print("\n🎉 Database seeded successfully!")
        print("   Run: uvicorn app.main:app --reload")
        print("   Docs: http://localhost:8000/api/docs")

    except Exception as e:
        db.rollback()
        print(f"\n❌ Seed failed: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
