select
    date_trunc('month', f.invoice_date)::date as month,
    f.product_key,
    p.stock_code,
    p.product_name,
    p.category,
    round({{ net_revenue('f.') }}, 2) as net_revenue,
    round({{ gross_sales('f.') }}, 2) as gross_sales,
    round({{ returns_value('f.') }}, 2) as returns_value,
    sum(f.quantity) filter (where not f.is_cancellation) as units_sold,
    count(distinct f.invoice_no) filter (where not f.is_cancellation) as orders,
    round(avg(f.unit_price) filter (where not f.is_cancellation), 4) as avg_unit_price
from {{ ref('fact_sales') }} f
join {{ ref('dim_product') }} p using (product_key)
where f.line_type = 'product'
group by all
