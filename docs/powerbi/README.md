# Power BI integration

The pipeline prepares a Power BI-ready star schema. **No Power BI report has been
generated or published by this project** — Power BI Desktop/Service is not part of the
environment. This folder documents how to connect and a specification for the report.

## 1. Get the model

| Option | How | When |
|---|---|---|
| Parquet files | Pipeline stage `refresh_exports` writes `data/exports/powerbi/*.parquet` (+ CSV for small tables). In Power BI Desktop: *Get Data › Parquet*, one query per table. | Local / portfolio |
| PostgreSQL | Set `BP_POSTGRES_DSN`; stage `publish_postgres` copies every gold table to schema `gold`. *Get Data › PostgreSQL*, user `bi_reader` (read-only). | Shared / scheduled refresh via gateway |
| Athena / Redshift | `python cloud/aws/deploy.py --bucket … --apply`, then the Athena ODBC driver or the native Redshift connector. | Cloud |

## 2. Relationships (all many-to-one, single direction, from fact to dimension)

```
fact_sales[date_key]      → dim_date[date_key]
fact_sales[customer_key]  → dim_customer[customer_key]
fact_sales[product_key]   → dim_product[product_key]
fact_sales[geography_key] → dim_geography[geography_key]
fact_orders[date_key]     → dim_date[date_key]
fact_orders[customer_key] → dim_customer[customer_key]
fact_marketing[campaign_key] → dim_campaign[campaign_key]   (synthetic)
fact_inventory[product_key]  → dim_product[product_key]     (synthetic)
```

Mark `dim_date` as the date table (`date_day`). Hide surrogate keys. Format currency as GBP.

## 3. Measures

`measures.dax` contains the measure library: revenue, returns, orders, AOV, date
intelligence (MTD/QTD/YTD, PM, PY, rolling 3M), MoM/YoY variance, budget vs actual,
price/volume bridge, customer acquisition, repeat rate and month-over-month retention.
Definitions match `businesspulse/analytics/kpis.py`; the dbt test
`assert_kpi_monthly_matches_fact` guarantees the warehouse totals they should reproduce.

**Validation tip:** after building the model, a card with `[Net Revenue]` for Nov 2011
should equal `gold.mart_kpi_monthly.net_revenue` for `2011-11` (query it in the SQL Studio).

## 4. Report specification

| Page | Visuals | Measures / fields | Filters |
|---|---|---|---|
| Executive | KPI cards (Net Revenue, Orders, AOV, Active Customers, Retention Rate, Return Rate) with PY variance; monthly revenue column chart; KPI vs target table | Net Revenue, Revenue YoY Variance %, Revenue vs Target % | Date slicer, Region |
| Sales | Revenue trend line (week); category bar; country map (exclude UK toggle via bookmark); product Pareto table | Net Revenue, Units Sold, Returns Value | Date, Category, Country |
| Customers | New vs returning stacked column; RFM segment bar; cohort matrix (matrix visual, `mart_cohort_retention`) | New/Returning Customers, Repeat Purchase Rate | Date, Segment, Loyalty tier |
| Variance | Waterfall (Order Volume Effect, AOV Effect, returns); variance by country/category | Revenue MoM/YoY Variance | Date |
| Marketing *(synthetic banner)* | Spend by channel; ROAS bar; CVR line | ROAS (synthetic), Conversion Rate (synthetic) | Date, Channel |
| Inventory *(synthetic banner)* | Stockouts by warehouse; turnover by category | Inventory Turnover (synthetic) | Warehouse |

Every page using `fact_marketing` or `fact_inventory` must carry a visible
"Synthetic demonstration data" text box — the rows carry `is_synthetic = TRUE`.

## 5. Row-level security (optional)

Create role *Regional Manager* with DAX filter on `dim_geography`:
`[region] = LOOKUPVALUE(user_region[region], user_region[email], USERPRINCIPALNAME())`.
