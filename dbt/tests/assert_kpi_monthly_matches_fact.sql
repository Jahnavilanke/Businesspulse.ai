-- Business test: monthly KPI net revenue equals the sum of product-line revenue in fact_sales.
with k as (select sum(net_revenue) as rev from {{ ref('mart_kpi_monthly') }}),
     f as (select sum(line_revenue) as rev from {{ ref('fact_sales') }} where line_type = 'product')
select * from k, f where abs(k.rev - f.rev) > 1.0
