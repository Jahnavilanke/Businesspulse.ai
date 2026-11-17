-- Acquisition-month cohorts: share of each cohort active N months after first purchase.
with co as (select * from {{ ref('int_customer_orders') }}),
firsts as (
    select customer_id, date_trunc('month', min(first_order_date))::date as cohort_month
    from co group by 1
),
activity as (
    select distinct co.customer_id, f.cohort_month, date_trunc('month', co.invoice_date)::date as activity_month
    from co join firsts f using (customer_id)
),
sized as (select cohort_month, count(*) as cohort_size from firsts group by 1)
select
    a.cohort_month,
    strftime(a.cohort_month, '%Y-%m') as cohort,
    datediff('month', a.cohort_month, a.activity_month) as months_since_first,
    count(distinct a.customer_id) as active_customers,
    s.cohort_size,
    round(count(distinct a.customer_id) * 1.0 / s.cohort_size, 4) as retention_rate
from activity a join sized s using (cohort_month)
group by a.cohort_month, cohort, months_since_first, s.cohort_size
order by 1, 3
