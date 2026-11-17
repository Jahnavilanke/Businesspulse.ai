select
    date_trunc('month', f.invoice_date)::date as month,
    g.geography_key,
    g.country_name,
    g.region,
    g.market,
    round({{ net_revenue('f.') }}, 2) as net_revenue,
    round({{ gross_sales('f.') }}, 2) as gross_sales,
    round({{ returns_value('f.') }}, 2) as returns_value,
    {{ order_count('f.') }} as orders,
    count(distinct f.customer_key) filter (where f.customer_key <> -1) as customers
from {{ ref('fact_sales') }} f
join {{ ref('dim_geography') }} g using (geography_key)
group by all
