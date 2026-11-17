{{ config(enabled = var('enable_synthetic')) }}
-- SYNTHETIC stock levels joined to ACTUAL trailing demand. Every row is flagged is_synthetic
-- because stock quantities are generated; the demand columns come from real transactions.
with snap as (select * from {{ ref('stg_inventory_snapshot') }}),
last_d as (select max(invoice_date) as d from {{ ref('stg_transactions') }}),
demand as (
    select stock_code,
           sum(quantity) filter (where invoice_date > (select d from last_d) - 90) as units_last_90d,
           sum(quantity) filter (where invoice_date > (select d from last_d) - 365) as units_last_365d
    from {{ ref('stg_transactions') }}
    where line_type = 'product' and not is_cancellation
    group by 1
),
wh as (select count(distinct warehouse_id) as n_wh from snap)
select
    s.snapshot_date,
    {{ date_key('s.snapshot_date') }} as date_key,
    md5(s.stock_code) as product_key,
    s.stock_code,
    s.warehouse_id,
    s.stock_qty,
    s.reorder_level,
    s.reorder_qty,
    s.supplier_id,
    s.supplier_lead_time_days,
    coalesce(d.units_last_90d, 0) as units_last_90d_actual,
    coalesce(d.units_last_365d, 0) as units_last_365d_actual,
    round(coalesce(d.units_last_90d, 0) / 90.0 / wh.n_wh, 3) as avg_daily_demand_per_wh,
    round(s.stock_qty / nullif(coalesce(d.units_last_90d, 0) / 90.0 / wh.n_wh, 0), 1) as days_of_cover,
    s.stock_qty = 0 as is_stockout,
    s.stock_qty <= s.reorder_level as below_reorder_level,
    coalesce(d.units_last_90d, 0) = 0 as is_slow_moving,
    true as is_synthetic
from snap s
left join demand d using (stock_code)
cross join wh
