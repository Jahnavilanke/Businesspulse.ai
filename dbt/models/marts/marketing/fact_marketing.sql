{{ config(enabled = var('enable_synthetic')) }}
-- SYNTHETIC marketing performance. Attributed revenue is generated, NOT derived from real orders.
select
    md5(d.campaign_id) as campaign_key,
    d.campaign_id,
    {{ date_key('d.activity_date') }} as date_key,
    d.activity_date,
    c.channel,
    d.cost,
    d.impressions,
    d.clicks,
    d.conversions,
    d.attributed_revenue,
    {{ safe_divide('d.clicks', 'd.impressions') }} as ctr,
    {{ safe_divide('d.conversions', 'd.clicks') }} as conversion_rate,
    {{ safe_divide('d.attributed_revenue', 'd.cost') }} as roas,
    true as is_synthetic
from {{ ref('stg_marketing_daily') }} d
join {{ ref('stg_marketing_campaigns') }} c using (campaign_id)
