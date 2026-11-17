-- Customer purchase sequence (identified customers, non-cancelled orders).
select
    o.*,
    row_number() over (partition by customer_id order by invoice_ts, invoice_no) as order_seq,
    min(invoice_date) over (partition by customer_id) as first_order_date,
    lag(invoice_date) over (partition by customer_id order by invoice_ts, invoice_no) as prev_order_date
from {{ ref('int_orders') }} o
where customer_id is not null and not is_cancellation
