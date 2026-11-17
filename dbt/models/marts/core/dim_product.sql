select
    md5(p.stock_code) as product_key,
    p.stock_code,
    p.product_name,
    p.category,
    p.category_method,
    p.typical_unit_price as list_price_gbp,
    null::double as unit_cost_gbp,       -- not available in the source data (no cost => no actual margin)
    p.first_sold_date,
    p.last_sold_date,
    p.description_variants
from {{ ref('int_products') }} p
union all
select md5('UNKNOWN'), 'UNKNOWN', 'Non-product / unknown', 'Non-product', 'n/a', null, null, null, null, 0
