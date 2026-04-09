"""
POS Service — Phase 2
Handles:
  - Sale transaction creation with FIFO (expiry-ascending) inventory deduction
  - Hard Lock enforcement: transactions cannot be voided after 2 minutes
  - Receipt assembly
  - Daily sales reporting
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from fastapi import HTTPException

from app.models.models import StockBatch, StockTransaction, TransactionType, Drug
from app.models.transaction import SaleTransaction, SaleTransactionItem, TransactionStatus
from app.services.fx_service import get_cached_fx_rate
from app.services.clinical_service import check_patient_allergies

VOID_WINDOW_SECONDS = 120  # 2-minute hard lock


# ─────────────────────────────────────────────
# CREATE TRANSACTION
# ─────────────────────────────────────────────

def create_sale_transaction(
    db: Session,
    pharmacist: str,
    items: list[dict],          # [{drug_id, quantity, unit_price_ngn}]
    patient_id: Optional[int],
    payment_method: str,
    notes: Optional[str],
) -> SaleTransaction:
    """
    Create a POS sale transaction.

    For each line item:
      1. Validate drug + stock availability (FIFO by expiry_date ascending).
      2. Deduct quantity from StockBatch(es).
      3. Record a StockTransaction audit row.

    Returns the persisted SaleTransaction with items populated.
    """
    if not items:
        raise HTTPException(status_code=400, detail="Transaction must contain at least one item.")

    # ── Allergy Hard Block (Phase 4) ────────────────────────────────────
    # Run BEFORE any stock deduction or DB writes.
    # Walk-in transactions (patient_id=None) skip this check.
    if patient_id is not None:
        drug_ids_in_basket = [item["drug_id"] for item in items]
        conflicts = check_patient_allergies(patient_id, drug_ids_in_basket, db)
        if conflicts:
            first = conflicts[0]
            raise HTTPException(
                status_code=409,
                detail={
                    "blocked":  True,
                    "reason":   "patient_allergy",
                    "allergen": first["allergen"],
                    "drug":     first["drug_name"],
                    "all_conflicts": conflicts,
                },
            )

    fx_rate = Decimal(str(get_cached_fx_rate()))
    total_ngn = Decimal("0")
    line_records = []

    for item in items:
        drug_id        = item["drug_id"]
        qty_needed     = item["quantity"]
        unit_price_ngn = Decimal(str(item["unit_price_ngn"]))

        if qty_needed <= 0:
            raise HTTPException(status_code=400, detail=f"Quantity must be positive (drug_id={drug_id}).")
        if unit_price_ngn <= 0:
            raise HTTPException(status_code=400, detail=f"Unit price must be positive (drug_id={drug_id}).")

        # Verify drug exists
        drug = db.query(Drug).filter(Drug.id == drug_id, Drug.is_active == True).first()
        if not drug:
            raise HTTPException(status_code=404, detail=f"Drug id={drug_id} not found or inactive.")

        # FIFO: oldest expiry first, must have enough quantity
        remaining = qty_needed
        deductions = []  # list of (batch, deduct_qty)

        batches = (
            db.query(StockBatch)
            .filter(
                StockBatch.drug_id == drug_id,
                StockBatch.quantity > 0,
            )
            .order_by(StockBatch.expiry_date.asc())
            .all()
        )

        for batch in batches:
            if remaining <= 0:
                break
            take = min(batch.quantity, remaining)
            deductions.append((batch, take))
            remaining -= take

        if remaining > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for drug_id={drug_id}. "
                       f"Requested {qty_needed}, available {qty_needed - remaining}.",
            )

        item_total = unit_price_ngn * qty_needed
        total_ngn += item_total

        # Use the primary batch (first FIFO batch) as the line-item reference
        primary_batch, primary_qty = deductions[0]
        line_records.append({
            "drug_id":        drug_id,
            "batch_id":       primary_batch.id,
            "quantity":       qty_needed,
            "unit_price_ngn": unit_price_ngn,
            "total_ngn":      item_total,
            "deductions":     deductions,
        })

    # ── Persist transaction header ──────────────────────────────────────
    txn = SaleTransaction(
        patient_id=patient_id,
        pharmacist=pharmacist,
        payment_method=payment_method,
        total_ngn=total_ngn,
        fx_rate=fx_rate,
        status=TransactionStatus.open,
        notes=notes,
    )
    db.add(txn)
    db.flush()  # populate txn.id before adding children

    # ── Persist line items + deduct stock ───────────────────────────────
    for rec in line_records:
        line = SaleTransactionItem(
            transaction_id=txn.id,
            drug_id=rec["drug_id"],
            batch_id=rec["batch_id"],
            quantity=rec["quantity"],
            unit_price_ngn=rec["unit_price_ngn"],
            total_ngn=rec["total_ngn"],
        )
        db.add(line)

        for batch, take_qty in rec["deductions"]:
            new_qty = batch.quantity - take_qty
            batch.quantity = new_qty

            audit = StockTransaction(
                batch_id=batch.id,
                transaction_type=TransactionType.sale,
                quantity_change=-take_qty,
                balance_after=new_qty,
                retail_price_ngn=rec["unit_price_ngn"],
                fx_rate_used=fx_rate,
                notes=f"POS sale txn_id={txn.id}",
            )
            db.add(audit)

    db.commit()
    db.refresh(txn)
    return txn


# ─────────────────────────────────────────────
# GET TRANSACTION / RECEIPT
# ─────────────────────────────────────────────

def get_transaction(db: Session, transaction_id: int) -> SaleTransaction:
    txn = db.query(SaleTransaction).filter(SaleTransaction.id == transaction_id).first()
    if not txn:
        raise HTTPException(status_code=404, detail=f"Transaction id={transaction_id} not found.")
    return txn


def assemble_receipt(txn: SaleTransaction) -> dict:
    """
    Build a human-readable receipt dict from a SaleTransaction ORM object.
    """
    items_out = []
    for item in txn.items:
        items_out.append({
            "drug_id":        item.drug_id,
            "drug_name":      item.drug.generic_name if item.drug else None,
            "brand_name":     item.drug.brand_name if item.drug else None,
            "batch_no":       item.batch.batch_no if item.batch else None,
            "quantity":       item.quantity,
            "unit_price_ngn": float(item.unit_price_ngn),
            "total_ngn":      float(item.total_ngn),
        })

    return {
        "transaction_id": txn.id,
        "status":         txn.status,
        "pharmacist":     txn.pharmacist,
        "patient_id":     txn.patient_id,
        "payment_method": txn.payment_method,
        "items":          items_out,
        "total_ngn":      float(txn.total_ngn),
        "fx_rate":        float(txn.fx_rate),
        "notes":          txn.notes,
        "created_at":     txn.created_at.isoformat(),
        "void_deadline":  (txn.created_at + timedelta(seconds=VOID_WINDOW_SECONDS)).isoformat(),
    }


# ─────────────────────────────────────────────
# VOID TRANSACTION (HARD LOCK)
# ─────────────────────────────────────────────

def void_transaction(db: Session, transaction_id: int) -> dict:
    """
    Void a transaction.
    Raises HTTP 409 if the transaction is older than VOID_WINDOW_SECONDS (Hard Lock).
    Restores deducted stock on success.
    """
    txn = get_transaction(db, transaction_id)

    if txn.status == TransactionStatus.voided:
        raise HTTPException(status_code=409, detail="Transaction is already voided.")

    if txn.status == TransactionStatus.locked:
        raise HTTPException(
            status_code=409,
            detail=f"Transaction id={transaction_id} is hard-locked "
                   f"(more than {VOID_WINDOW_SECONDS // 60} minutes have passed). Cannot void.",
        )

    # Enforce the 2-minute window dynamically (status may still be 'open')
    now = datetime.now(timezone.utc)
    created = txn.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    age_seconds = (now - created).total_seconds()
    if age_seconds > VOID_WINDOW_SECONDS:
        # Mark as locked in DB for future requests, then reject
        txn.status = TransactionStatus.locked
        db.commit()
        raise HTTPException(
            status_code=409,
            detail=f"Transaction id={transaction_id} is hard-locked "
                   f"(created {int(age_seconds)}s ago, limit is {VOID_WINDOW_SECONDS}s). Cannot void.",
        )

    # ── Restore stock ───────────────────────────────────────────────────
    for item in txn.items:
        batch = db.query(StockBatch).filter(StockBatch.id == item.batch_id).first()
        if batch:
            new_qty = batch.quantity + item.quantity
            batch.quantity = new_qty
            db.add(StockTransaction(
                batch_id=batch.id,
                transaction_type=TransactionType.adjustment,
                quantity_change=+item.quantity,
                balance_after=new_qty,
                notes=f"Void of POS txn_id={txn.id}",
            ))

    txn.status = TransactionStatus.voided
    db.commit()
    return {"detail": f"Transaction id={transaction_id} voided successfully."}


# ─────────────────────────────────────────────
# DAILY SALES REPORT
# ─────────────────────────────────────────────

def daily_sales_report(db: Session, report_date: str) -> dict:
    """
    Return aggregate sales for a given date (YYYY-MM-DD).
    Only counts non-voided transactions.
    """
    from datetime import date as date_type

    try:
        target = date_type.fromisoformat(report_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")

    # Filter transactions created on the target date (UTC)
    txns = (
        db.query(SaleTransaction)
        .filter(
            SaleTransaction.status != TransactionStatus.voided,
            func.date(SaleTransaction.created_at) == target,
        )
        .all()
    )

    transaction_count = len(txns)
    total_revenue_ngn = sum(float(t.total_ngn) for t in txns)
    total_items_sold  = sum(
        sum(i.quantity for i in t.items) for t in txns
    )

    # Per-drug breakdown
    drug_breakdown: dict[int, dict] = {}
    for txn in txns:
        for item in txn.items:
            entry = drug_breakdown.setdefault(item.drug_id, {
                "drug_id":   item.drug_id,
                "drug_name": item.drug.generic_name if item.drug else None,
                "quantity":  0,
                "revenue_ngn": 0.0,
            })
            entry["quantity"]    += item.quantity
            entry["revenue_ngn"] += float(item.total_ngn)

    return {
        "date":              report_date,
        "transaction_count": transaction_count,
        "total_revenue_ngn": round(total_revenue_ngn, 2),
        "total_items_sold":  total_items_sold,
        "drugs":             sorted(drug_breakdown.values(), key=lambda x: x["revenue_ngn"], reverse=True),
    }
