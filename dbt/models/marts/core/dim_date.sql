-- Calendar dimension covering the full transaction period (+ forecast horizon).
with bounds as (
    select min(invoice_date) as d0, max(invoice_date) + interval 365 day as d1 from {{ ref('stg_transactions') }}
),
days as (
    select unnest(generate_series(d0::timestamp, d1::timestamp, interval 1 day))::date as date_day from bounds
)
select
    {{ date_key('date_day') }} as date_key,
    date_day,
    extract(year from date_day)::integer as year,
    extract(quarter from date_day)::integer as quarter,
    extract(month from date_day)::integer as month,
    strftime(date_day, '%b') as month_name,
    strftime(date_day, '%Y-%m') as year_month,
    date_trunc('month', date_day)::date as month_start,
    date_trunc('week', date_day)::date as week_start,
    extract(isodow from date_day)::integer as iso_day_of_week,
    strftime(date_day, '%a') as day_name,
    extract(isodow from date_day) in (6, 7) as is_weekend,
    date_day > (select max(invoice_date) from {{ ref('stg_transactions') }}) as is_future
from days
