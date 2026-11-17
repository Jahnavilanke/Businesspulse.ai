-- Example Athena queries over the Glue-registered gold layer (database businesspulse_gold).
-- Scan cost: Athena bills per TB scanned; Parquet + column pruning keeps these to a few MB.

-- Monthly KPIs
select year_month, net_revenue, orders, avg_order_value, retention_rate
from businesspulse_gold.mart_kpi_monthly order by year_month;

-- Revenue by region (star join)
select g.region, sum(f.line_revenue) as net_revenue
from businesspulse_gold.fact_sales f
join businesspulse_gold.dim_geography g on f.geography_key = g.geography_key
where f.line_type = 'product'
group by g.region order by net_revenue desc;

-- Category performance, last 90 days of data
select p.category, sum(f.line_revenue) as net_revenue, sum(f.quantity) as units
from businesspulse_gold.fact_sales f
join businesspulse_gold.dim_product p on f.product_key = p.product_key
where f.line_type = 'product'
  and f.invoice_date > (select max(invoice_date) - interval '90' day from businesspulse_gold.fact_sales)
group by p.category order by net_revenue desc;
