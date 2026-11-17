select
    o.invoice_no,
    {{ date_key('o.invoice_date') }} as date_key,
    o.invoice_date,
    o.invoice_ts,
    coalesce(o.customer_id, -1) as customer_key,
    md5(o.country_raw) as geography_key,
    o.is_cancellation,
    o.order_revenue,
    o.units,
    o.line_count,
    o.distinct_products,
    co.order_seq,
    case when o.customer_id is null then 'Guest'
         when co.order_seq = 1 then 'New'
         when co.order_seq > 1 then 'Returning'
         else 'Cancellation' end as customer_type
from {{ ref('int_orders') }} o
left join {{ ref('int_customer_orders') }} co using (invoice_no)
