{{ config(enabled = var('enable_synthetic')) }}
-- SILVER: SYNTHETIC campaign master (demonstration only).
select campaign_id, campaign_name, channel, objective, start_date::date as start_date, end_date::date as end_date,
       target_region, planned_budget::double as planned_budget, true as is_synthetic
from {{ lake('synthetic/marketing/campaigns.parquet') }}
