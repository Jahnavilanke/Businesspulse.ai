-- Customer dimension with DERIVED attributes (the source has no CRM data):
--   registration_date  := first purchase date
--   home_country       := most frequent invoice country
--   rfm_segment        := RFM quintile rules as of the last data date
--   loyalty_tier       := lifetime net revenue percentile (derived, not a real loyalty program)
with orders as (select * from {{ ref('int_orders') }} where customer_id is not null),
all_customers as (   -- every identified customer on any validated line (incl. cancellation-only customers)
    select customer_id, mode(country_raw) as any_country
    from {{ ref('stg_transactions') }} where customer_id is not null group by 1
),
as_of as (select max(invoice_date) + 1 as as_of_date from {{ ref('stg_transactions') }}),
base as (
    select
        customer_id,
        min(invoice_date) filter (where not is_cancellation) as first_purchase_date,
        max(invoice_date) filter (where not is_cancellation) as last_purchase_date,
        count(*) filter (where not is_cancellation) as lifetime_orders,
        sum(order_revenue) as lifetime_net_revenue,
        sum(order_revenue) filter (where is_cancellation) as lifetime_returns,
        mode(country_raw) as home_country
    from orders group by customer_id
),
base_all as (
    select a.customer_id, b.first_purchase_date, b.last_purchase_date, coalesce(b.lifetime_orders, 0) as lifetime_orders,
           coalesce(b.lifetime_net_revenue, 0) as lifetime_net_revenue, b.lifetime_returns,
           coalesce(b.home_country, a.any_country) as home_country
    from all_customers a left join base b using (customer_id)
),
rfm as (
    select b.*,
        (select as_of_date from as_of) - b.last_purchase_date as recency_days,
        ntile(5) over (order by (select as_of_date from as_of) - b.last_purchase_date desc) as r_score,
        ntile(5) over (order by b.lifetime_orders) as f_score,
        ntile(5) over (order by b.lifetime_net_revenue) as m_score,
        percent_rank() over (order by b.lifetime_net_revenue) as revenue_pct_rank
    from base_all b where b.first_purchase_date is not null
),
no_purchase as (
    select b.*, null::integer as recency_days, null::bigint as r_score, null::bigint as f_score, null::bigint as m_score,
           null::double as revenue_pct_rank
    from base_all b where b.first_purchase_date is null
),
scored as (select * from rfm union all by name select * from no_purchase)
select
    customer_id as customer_key,
    customer_id,
    first_purchase_date as registration_date,
    first_purchase_date,
    last_purchase_date,
    lifetime_orders,
    round(lifetime_net_revenue, 2) as lifetime_net_revenue,
    round(coalesce(-lifetime_returns, 0), 2) as lifetime_returns,
    home_country,
    md5(home_country) as geography_key,
    recency_days,
    r_score, f_score, m_score,
    case
        when first_purchase_date is null then 'No Completed Purchase'
        when r_score >= 4 and f_score >= 4 and m_score >= 4 then 'Champions'
        when r_score >= 3 and f_score >= 3 then 'Loyal'
        when r_score >= 4 and f_score <= 2 then 'New / Promising'
        when r_score = 3 and f_score <= 2 then 'Needs Attention'
        when r_score <= 2 and f_score >= 3 then 'At Risk'
        when r_score = 2 and f_score <= 2 then 'Hibernating'
        else 'Lost'
    end as rfm_segment,
    case
        when first_purchase_date is null then 'n/a'
        when revenue_pct_rank >= 0.95 then 'Platinum'
        when revenue_pct_rank >= 0.80 then 'Gold'
        when revenue_pct_rank >= 0.50 then 'Silver'
        else 'Bronze'
    end as loyalty_tier,
    case when lifetime_orders > 1 then true else false end as is_repeat_customer,
    'derived' as attribute_basis
from scored
union all
select -1, null, null, null, null, null, null, null, 'Unspecified', md5('Unspecified'), null, null, null, null,
       'Guest / Unidentified', 'n/a', false, 'n/a'
