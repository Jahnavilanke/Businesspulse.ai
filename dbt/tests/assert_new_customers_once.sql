-- Business test: a customer can be 'New' in exactly one order.
select customer_key, count(*) as n
from {{ ref('fact_orders') }} where customer_type = 'New'
group by 1 having count(*) > 1
