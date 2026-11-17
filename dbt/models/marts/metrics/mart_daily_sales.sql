select
    f.invoice_date as date_day,
    round({{ net_revenue('f.') }}, 2) as net_revenue,
    round({{ gross_sales('f.') }}, 2) as gross_sales,
    round({{ returns_value('f.') }}, 2) as returns_value,
    {{ order_count('f.') }} as orders,
    sum(f.quantity) filter (where f.line_type = 'product' and not f.is_cancellation) as units_sold,
    count(distinct f.customer_key) filter (where f.customer_key <> -1 and not f.is_cancellation) as customers
from {{ ref('fact_sales') }} f
group by 1
order by 1
