-- Invoice-level grain (product lines only).
select
    invoice_no,
    any_value(customer_id) as customer_id,
    any_value(country_raw) as country_raw,
    min(invoice_ts) as invoice_ts,
    min(invoice_date) as invoice_date,
    bool_or(is_cancellation) as is_cancellation,
    sum(line_revenue) as order_revenue,
    sum(quantity) as units,
    count(*) as line_count,
    count(distinct stock_code) as distinct_products
from {{ ref('stg_transactions') }}
where line_type = 'product'
group by invoice_no
