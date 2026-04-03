-- Pharmacy Intelligence Mainframe — PostgreSQL Init
-- Runs once on first container start

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- Fuzzy drug name search

-- ─── Performance Indexes ────────────────────────────────────────────────────

-- Drug search (GIN index for trigram fuzzy search)
CREATE INDEX IF NOT EXISTS idx_drugs_brand_trgm ON drugs USING gin (brand_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_drugs_generic_trgm ON drugs USING gin (generic_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_drugs_nafdac ON drugs (nafdac_reg_no);

-- Stock lookups
CREATE INDEX IF NOT EXISTS idx_stock_drug_id ON stock_batches (drug_id);
CREATE INDEX IF NOT EXISTS idx_stock_expiry ON stock_batches (expiry_date);
CREATE INDEX IF NOT EXISTS idx_stock_batch_no ON stock_batches (batch_no);
CREATE INDEX IF NOT EXISTS idx_stock_status ON stock_batches (status);

-- Transaction analysis
CREATE INDEX IF NOT EXISTS idx_tx_created ON stock_transactions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tx_batch ON stock_transactions (batch_id);
CREATE INDEX IF NOT EXISTS idx_tx_type ON stock_transactions (transaction_type);

-- Patient lookups
CREATE INDEX IF NOT EXISTS idx_patient_phone ON patients (phone_number);
CREATE INDEX IF NOT EXISTS idx_refill_next_date ON refill_schedules (next_refill_date);
CREATE INDEX IF NOT EXISTS idx_refill_patient ON refill_schedules (patient_id);

-- Dispensing revenue queries
CREATE INDEX IF NOT EXISTS idx_dispense_created ON dispensing_records (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dispense_patient ON dispensing_records (patient_id);

-- ─── Views ──────────────────────────────────────────────────────────────────

-- Sales velocity: daily burn rate per drug (last 7 days)
CREATE OR REPLACE VIEW v_stock_velocity AS
SELECT
    d.id AS drug_id,
    d.brand_name,
    d.generic_name,
    COALESCE(SUM(ABS(t.quantity_change)) FILTER (
        WHERE t.transaction_type = 'sale'
        AND t.created_at >= NOW() - INTERVAL '7 days'
    ), 0) AS units_sold_7d,
    COALESCE(SUM(ABS(t.quantity_change)) FILTER (
        WHERE t.transaction_type = 'sale'
        AND t.created_at >= NOW() - INTERVAL '7 days'
    ), 0) / 7.0 AS burn_rate_per_day,
    COALESCE(SUM(b.quantity), 0) AS total_stock
FROM drugs d
LEFT JOIN stock_batches b ON b.drug_id = d.id AND b.status NOT IN ('expired', 'out')
LEFT JOIN stock_transactions t ON t.batch_id = b.id
WHERE d.is_active = TRUE
GROUP BY d.id, d.brand_name, d.generic_name;

-- Low stock alerts view
CREATE OR REPLACE VIEW v_low_stock_alerts AS
SELECT
    v.drug_id,
    v.brand_name,
    v.generic_name,
    v.total_stock,
    v.burn_rate_per_day,
    CASE
        WHEN v.burn_rate_per_day > 0 THEN ROUND(v.total_stock / v.burn_rate_per_day)
        ELSE 9999
    END AS days_remaining,
    CASE
        WHEN v.burn_rate_per_day > 0 AND (v.total_stock / v.burn_rate_per_day) < 3 THEN 'CRITICAL'
        WHEN v.burn_rate_per_day > 0 AND (v.total_stock / v.burn_rate_per_day) < 7 THEN 'LOW'
        ELSE 'OK'
    END AS stock_status
FROM v_stock_velocity v
WHERE v.total_stock < 50
ORDER BY days_remaining ASC;

-- Daily revenue summary
CREATE OR REPLACE VIEW v_daily_revenue AS
SELECT
    DATE(created_at AT TIME ZONE 'Africa/Lagos') AS sale_date,
    COUNT(*) AS transaction_count,
    SUM(total_ngn) AS total_revenue_ngn,
    AVG(total_ngn) AS avg_basket_ngn
FROM dispensing_records
WHERE is_refund = FALSE
GROUP BY DATE(created_at AT TIME ZONE 'Africa/Lagos')
ORDER BY sale_date DESC;

-- Expiry watchdog view
CREATE OR REPLACE VIEW v_expiry_alerts AS
SELECT
    b.id AS batch_id,
    b.batch_no,
    d.brand_name,
    d.generic_name,
    b.quantity,
    b.expiry_date,
    (b.expiry_date - CURRENT_DATE) AS days_to_expiry,
    CASE
        WHEN b.expiry_date < CURRENT_DATE THEN 'EXPIRED'
        WHEN b.expiry_date <= CURRENT_DATE + 30 THEN 'CRITICAL'
        WHEN b.expiry_date <= CURRENT_DATE + 90 THEN 'PROMOTE'
        ELSE 'OK'
    END AS expiry_status
FROM stock_batches b
JOIN drugs d ON d.id = b.drug_id
WHERE b.quantity > 0
  AND b.expiry_date <= CURRENT_DATE + 90
ORDER BY b.expiry_date ASC;

-- Patient refill calendar
CREATE OR REPLACE VIEW v_refill_calendar AS
SELECT
    r.id,
    p.full_name AS patient_name,
    p.phone_number,
    d.brand_name AS drug_name,
    d.strength,
    r.next_refill_date,
    (r.next_refill_date - CURRENT_DATE) AS days_until_refill,
    r.standard_qty,
    r.cycle_days
FROM refill_schedules r
JOIN patients p ON p.id = r.patient_id
JOIN drugs d ON d.id = r.drug_id
WHERE r.is_active = TRUE
  AND p.is_active = TRUE
  AND r.next_refill_date >= CURRENT_DATE
ORDER BY r.next_refill_date ASC;

-- ─── Row-Level Security (optional, for multi-branch) ─────────────────────────
-- ALTER TABLE dispensing_records ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY branch_isolation ON dispensing_records
--   USING (branch_id = current_setting('app.branch_id')::integer);
