-- Product master derived from transaction lines. Category comes from documented
-- keyword rules (seed category_rules); the source data has no category field.
with lines as (
    select * from {{ ref('stg_transactions') }} where line_type = 'product'
),
agg as (
    select
        stock_code,
        mode(description) as product_name,
        median(unit_price) filter (where not is_cancellation) as typical_unit_price,
        min(invoice_date) as first_sold_date,
        max(invoice_date) as last_sold_date,
        count(distinct description) as description_variants
    from lines
    group by stock_code
),
rules as (select * from {{ ref('category_rules') }}),
matched as (
    select a.stock_code, r.category,
           row_number() over (partition by a.stock_code order by r.priority) as rn
    from agg a join rules r on regexp_matches(a.product_name, r.pattern)
)
select
    agg.*,
    coalesce(m.category, 'Other / Unclassified') as category,
    case when m.category is null then 'unmatched' else 'keyword_rule' end as category_method
from agg
left join matched m on m.stock_code = agg.stock_code and m.rn = 1
