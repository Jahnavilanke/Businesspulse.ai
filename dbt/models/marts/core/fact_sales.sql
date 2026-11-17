{{ config(
    materialized = 'incremental',
    unique_key = 'line_id',
    incremental_strategy = 'delete+insert',
    on_schema_change = 'append_new_columns'
) }}
-- Grain: one invoice line. Includes cancellations (negative quantity) and non-product
-- lines (postage, fees, adjustments) flagged by line_type so metrics can filter explicitly.
with lines as (
    select * from {{ ref('stg_transactions') }}
    {% if is_incremental() %}
      -- reprocess a 3-day lookback window to absorb late-arriving lines
      where invoice_ts > (select max(invoice_ts) - interval 3 day from {{ this }})
    {% endif %}
)
select
    l.line_id,
    l.invoice_no,
    {{ date_key('l.invoice_date') }} as date_key,
    l.invoice_date,
    l.invoice_ts,
    coalesce(l.customer_id, -1) as customer_key,
    l.customer_id,
    case when l.line_type = 'product' then md5(l.stock_code) else md5('UNKNOWN') end as product_key,
    l.stock_code,
    md5(l.country_raw) as geography_key,
    l.country_raw,
    'Online' as sales_channel,             -- the source retailer is a single online channel
    l.line_type,
    l.is_cancellation,
    l.quantity,
    l.unit_price,
    0.0::decimal(14,2) as discount_amount,   -- discounts appear as separate 'discount' lines in the source
    l.line_revenue,
    case when l.line_type = 'product' and not l.is_cancellation then l.line_revenue else 0 end as gross_sales,
    case when l.line_type = 'product' and l.is_cancellation then -l.line_revenue else 0 end as returns_value,
    l.batch_id,
    l.source_system
from lines l
