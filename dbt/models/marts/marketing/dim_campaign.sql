{{ config(enabled = var('enable_synthetic')) }}
-- SYNTHETIC campaign dimension (demonstration only).
select
    md5(campaign_id) as campaign_key,
    campaign_id,
    campaign_name,
    channel,
    objective,
    start_date,
    end_date,
    target_region,
    planned_budget,
    true as is_synthetic
from {{ ref('stg_marketing_campaigns') }}
