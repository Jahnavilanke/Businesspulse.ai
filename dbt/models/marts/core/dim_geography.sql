select
    md5(c.country_raw) as geography_key,
    c.country_raw,
    c.country_name,
    c.iso2,
    c.region,
    c.sub_region,
    c.is_domestic,
    case when c.is_domestic then 'Domestic (UK)' else 'International' end as market
from {{ ref('country_regions') }} c
