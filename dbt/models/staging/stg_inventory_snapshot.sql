{{ config(enabled = var('enable_synthetic')) }}
-- SILVER: SYNTHETIC inventory snapshot (demonstration only - not real stock data).
select
    snapshot_date::date as snapshot_date,
    upper(stock_code) as stock_code,
    warehouse_id,
    stock_qty::integer as stock_qty,
    reorder_level::integer as reorder_level,
    reorder_qty::integer as reorder_qty,
    supplier_id,
    supplier_lead_time_days::integer as supplier_lead_time_days,
    true as is_synthetic
from {{ lake('synthetic/inventory/inventory_snapshot.parquet') }}
