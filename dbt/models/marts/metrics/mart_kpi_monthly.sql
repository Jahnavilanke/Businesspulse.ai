-- Monthly business KPIs computed from fact tables (actual data only).
with f as (select * from {{ ref('fact_sales') }}),
o as (select * from {{ ref('fact_orders') }}),
last_day as (select max(invoice_date) as d from f),
sales as (
    select date_trunc('month', invoice_date)::date as month,
        {{ net_revenue() }} as net_revenue,
        {{ gross_sales() }} as gross_sales,
        {{ returns_value() }} as returns_value,
        {{ order_count() }} as orders,
        sum(quantity) filter (where line_type = 'product' and not is_cancellation) as units_sold,
        sum(line_revenue) filter (where line_type = 'postage') as postage_revenue,
        sum(line_revenue) filter (where line_type = 'product' and customer_id is null and not is_cancellation) as guest_gross_sales,
        count(distinct invoice_date) as trading_days
    from f group by 1
),
cust as (
    select date_trunc('month', invoice_date)::date as month,
        count(distinct customer_key) filter (where customer_key <> -1 and not is_cancellation) as active_customers,
        count(distinct customer_key) filter (where customer_type = 'New') as new_customers,
        count(distinct customer_key) filter (where customer_type = 'Returning') as returning_customers
    from o group by 1
),
active as (
    select distinct date_trunc('month', invoice_date)::date as month, customer_key
    from o where customer_key <> -1 and not is_cancellation
),
retention as (
    select cur.month, count(*) as retained_customers
    from active cur join active prev
      on prev.customer_key = cur.customer_key and prev.month = cur.month - interval 1 month
    group by cur.month
),
joined as (
    select s.*, c.active_customers, c.new_customers, c.returning_customers, r.retained_customers,
           lag(c.active_customers) over (order by s.month) as prev_active_customers
    from sales s
    left join cust c using (month)
    left join retention r using (month)
)
select
    month,
    strftime(month, '%Y-%m') as year_month,
    (month = date_trunc('month', (select d from last_day)) and (select d from last_day) < last_day(month)) as is_partial_month,
    trading_days,
    round(net_revenue, 2) as net_revenue,
    round(gross_sales, 2) as gross_sales,
    round(returns_value, 2) as returns_value,
    round({{ safe_divide('returns_value', 'gross_sales') }}, 4) as return_rate,
    orders,
    round({{ safe_divide('gross_sales', 'orders') }}, 2) as avg_order_value,
    units_sold,
    round(postage_revenue, 2) as postage_revenue,
    round({{ safe_divide('guest_gross_sales', 'gross_sales') }}, 4) as guest_sales_share,
    active_customers,
    new_customers,
    returning_customers,
    round({{ safe_divide('returning_customers', 'active_customers') }}, 4) as repeat_purchase_rate,
    retained_customers,
    prev_active_customers,
    round({{ safe_divide('retained_customers', 'prev_active_customers') }}, 4) as retention_rate
from joined
order by month
