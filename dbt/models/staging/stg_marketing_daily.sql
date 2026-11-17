{{ config(enabled = var('enable_synthetic')) }}
-- SILVER: SYNTHETIC daily campaign performance (demonstration only; not attributed to real orders).
select campaign_id, activity_date::date as activity_date, cost::double as cost, impressions::bigint as impressions,
       clicks::bigint as clicks, conversions::bigint as conversions, attributed_revenue::double as attributed_revenue,
       true as is_synthetic
from {{ lake('synthetic/marketing/campaign_daily.parquet') }}
