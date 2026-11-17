-- Business test: gold fact revenue must reconcile to silver within tolerance (no rows lost or double counted).
with s as (select sum(line_revenue) as rev, count(*) as n from {{ ref('stg_transactions') }}),
     f as (select sum(line_revenue) as rev, count(*) as n from {{ ref('fact_sales') }})
select * from s, f
where abs(s.rev - f.rev) > {{ var('revenue_tolerance') }} or s.n <> f.n
