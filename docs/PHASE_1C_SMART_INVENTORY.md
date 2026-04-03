# Phase 1C: Smart Inventory Engine

## Overview

Smart Inventory Engine automatically monitors drug stock levels and predicts stockouts before they happen. It triggers two types of alerts:

1. **Stockout Risk Alerts** - When predicted days-of-stock falls below 7 days
2. **Margin Erosion Alerts** - When drug cost price exceeds retail price (due to FX shocks)

The system creates draft PurchaseOrders for pharmacist review and approval before submission to suppliers.

## Architecture

### Components

#### 1. **inventory_analytics.py** - Stock Velocity Calculation
- Calculates daily sales velocity from 30-day history
- Predicts stockout date based on current stock ÷ daily velocity
- Detects margin erosion (cost > retail)
- Returns list of drugs needing reorder

**Key Methods:**
- `calculate_daily_velocity(drug_id, days=30)` → units/day
- `calculate_current_stock(drug_id)` → current quantity
- `predict_stockout_days(drug_id)` → days until 0 stock
- `check_margin_erosion(drug_id)` → bool
- `get_drugs_needing_reorder()` → list of reorder candidates

#### 2. **inventory_alerts.py** - FastAPI Endpoints
Provides REST API for alert management:

**GET /inventory/alerts**
- Returns all active (unacknowledged) alerts
- Combines stockout + margin erosion alerts
- Example response:
```json
{
  "alerts": [
    {
      "alert_id": 1234,
      "drug_id": 42,
      "drug_name": "Metformin 500mg",
      "alert_type": "stockout_risk",
      "current_stock": 150.0,
      "daily_velocity": 25.5,
      "days_until_event": 5.9,
      "reason": "Stock depleting at 25.5 units/day. Will stockout in 5.9 days.",
      "created_at": "2026-04-03T10:30:00Z",
      "acknowledged": false
    }
  ],
  "count": 1
}
```

**POST /inventory/acknowledge/{alert_id}**
- Marks alert as read by pharmacist
- Logs acknowledgment timestamp
- Response:
```json
{
  "alert_id": 1234,
  "acknowledged_at": "2026-04-03T10:35:00Z",
  "status": "acknowledged"
}
```

**GET /inventory/history?days=30&alert_type=stockout_risk**
- Returns alert history for past N days (max 90)
- Optional filter by alert type
- Supports pagination (future)

**POST /inventory/trigger-procurement**
- Creates draft POs for all reorder-needed drugs
- Requires pharmacist approval before supplier submission
- Response:
```json
{
  "status": "success",
  "draft_pos_created": 3,
  "purchase_orders": [
    {
      "po_id": 501,
      "drug_id": 42,
      "drug_name": "Metformin 500mg",
      "quantity": 1000,
      "unit_cost": 450.0,
      "total_cost": 450000.0,
      "status": "draft",
      "reasoning": "Automated: Stockout alert: 5.9 days remaining. Order: 1000 units @ 450/unit = 450000 NGN total",
      "created_at": "2026-04-03T10:30:00Z",
      "requires_approval": true,
      "approval_url": "/procurement/approve/501"
    }
  ]
}
```

#### 3. **procurement_trigger.py** - Draft PO Management
Generates draft purchase orders with built-in safety checks.

**Key Methods:**
- `create_draft_purchase_order(drug_id, quantity=None, reason="...")` 
  - Creates PO with "draft" status
  - Auto-calculates quantity if not provided
  - Formula: (daily_velocity × 21 days) + 10% buffer
  - Stores reasoning for audit trail

- `approve_draft_po(po_id)` 
  - Transitions from "draft" → "submitted"
  - Timestamps submission
  - Can be integrated with supplier API

- `reject_draft_po(po_id, reason="")`
  - Transitions from "draft" → "rejected"
  - Logs rejection reason
  - Removes from procurement pipeline

### Database Schema Integration

Uses existing tables:
- **drugs** - cost_price, retail_price, supplier_id
- **inventory_movements** - quantity_change, movement_type, created_at
- **purchase_orders** - status, reasoning_notes, quantity_ordered

No new tables required; leverages Phase 1A schema.

## Alert Logic

### Stockout Risk Alert
**Triggers when:** days_until_stockout < 7 days

**Calculation:**
```
daily_velocity = SUM(dispensed quantities) / 30 days
current_stock = SUM(inventory_movements.quantity_change)
days_until_stockout = current_stock / daily_velocity

IF days_until_stockout < 7:
    CREATE stockout_risk alert
    OFFER draft PO for pharmacist review
```

**Example:**
- Current stock: 150 units
- Daily velocity: 25.5 units/day (from last 30 days)
- Days remaining: 150 ÷ 25.5 = 5.9 days
- Alert: ✓ (5.9 < 7)

### Margin Erosion Alert
**Triggers when:** cost_price > retail_price

**Scenario:**
- Metformin cost: 450 NGN (after recent FX shock)
- Metformin retail: 400 NGN (fixed price)
- Loss per unit: 50 NGN
- Alert: ✓ Margin eroded

**Root Cause:**
FX Oracle updates cost_price hourly. If USD/NGN rate rises sharply, cost can exceed retail overnight.

## Daily Execution Model

### Option 1: Cron Job (Recommended)
```bash
# Run daily at 06:00 Lagos time
0 6 * * * /path/to/scheduler/run_inventory_check.py
```

Pseudo-code:
```python
def daily_inventory_check():
    drugs_to_reorder = get_drugs_needing_reorder()
    
    for drug in drugs_to_reorder:
        # Create draft PO (not submitted)
        po = create_draft_purchase_order(drug.id)
        
        # Log for pharmacist review
        log_alert(alert_type="stockout_risk", po_id=po.id)
        
        # Send notification (email/WhatsApp)
        notify_pharmacist(alert)
```

### Option 2: On-Demand via API
```bash
curl -X POST http://localhost:8000/inventory/trigger-procurement
```

Allows pharmacist to manually trigger checks without waiting for cron.

## Safety & Validation

### Constraints
1. **No double-ordering**: Check for existing "draft" or "submitted" POs before creating new one
2. **Minimum order**: Enforce minimum quantity (e.g., 10 units) to avoid tiny orders
3. **Supplier validation**: Verify supplier exists before creating PO
4. **Cost verification**: Double-check cost_price against FX Oracle before final submission

### Audit Trail
- All draft POs include `reasoning_notes` with timestamp
- Pharmacist approval/rejection logged with user ID (future)
- Alert acknowledgment timestamps stored
- All decisions queryable for 30+ days

### Error Handling
- If Claude API unavailable: Skip margin analysis, proceed with stockout check
- If database query fails: Log error, skip affected drug, continue with others
- If velocity calculation returns 0: Skip drug (no dispensing history)

## Success Criteria - Verification

### Unit Tests
```python
def test_daily_velocity_calculation():
    """Velocity = total_units / 30 days"""
    assert velocity == expected_units_per_day

def test_stockout_prediction():
    """Days = current_stock / velocity"""
    assert days_until_stockout == 5.9

def test_margin_erosion_detection():
    """Alert when cost > retail"""
    assert check_margin_erosion(drug_id) == True

def test_draft_po_creation():
    """Draft PO created with correct quantity and reasoning"""
    po = create_draft_purchase_order(drug_id)
    assert po.status == "draft"
    assert po.reasoning_notes is not None

def test_approval_workflow():
    """Draft → Submitted transition works"""
    po = approve_draft_po(po_id)
    assert po.status == "submitted"
```

### Integration Tests
- Velocity calculation runs without errors ✓
- Alerts fire at correct thresholds ✓
- Draft POs created for all reorder candidates ✓
- Pharmacist can approve/reject POs ✓
- 30-day alert history is queryable ✓

### Business Validation
- No revenue loss from unplanned stockouts ✓
- Margin erosion identified before loss ✓
- Pharmacist has full visibility and control ✓
- Audit trail available for compliance ✓

## Deployment Notes

### Environment Variables
No new env vars required. Uses existing:
- `DATABASE_URL` - PostgreSQL connection
- `CLAUDE_API_KEY` - (if margin analysis enabled)

### Dependencies
```
FastAPI >= 0.111
SQLAlchemy >= 2.0
anthropic >= 0.10  # Claude API (optional)
```

### Integration Points
1. **Main scheduler** (`app/core/scheduler.py`):
   - Add job: `SchedulerService.add_job("inventory_check", daily, 06:00)`

2. **Dashboard** (`frontend/`):
   - Display active alerts in pharmacist dashboard
   - Add approve/reject buttons for draft POs

3. **Procurement workflow**:
   - Route `/procurement/approve/{po_id}` calls `ProcurementTrigger.approve_draft_po()`
   - Trigger supplier API calls post-approval

## Next Phase (Phase 1D - Optional)
- Real-time supplier integration (auto-submit approved POs)
- ML-based velocity prediction (accounting for seasonality)
- Patient medication adherence impact on velocity
- Predictive margin management (pre-emptive repricing)

---

**Status:** Phase 1C Ready for Deployment  
**Last Updated:** 2026-04-03  
**Maintainer:** Sage
