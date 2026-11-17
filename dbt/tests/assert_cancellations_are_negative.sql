-- Business test: cancellation lines always reduce revenue.
select * from {{ ref('fact_sales') }} where is_cancellation and quantity >= 0
