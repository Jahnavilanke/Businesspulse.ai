-- SILVER: validated & standardized transaction lines produced by the quality stage.
select
    line_id,
    invoice_no,
    stock_code,
    description,
    quantity,
    unit_price,
    line_revenue,
    invoice_ts,
    invoice_date,
    customer_id,
    country_raw,
    line_type,
    is_cancellation,
    source_sheet,
    batch_id,
    ingested_at,
    'uci_online_retail_ii' as source_system
from {{ lake('silver/transactions/*.parquet') }}
