"""Data dictionary, business glossary and source catalog.

Column types are read live from the warehouse; business metadata (descriptions, source,
transformation rule, owner) is maintained here as code so it is reviewed with changes.
``python -m businesspulse.catalog`` regenerates docs/data_dictionary.md.
"""
from __future__ import annotations

import pandas as pd

from .analytics.kpis import metric_catalog
from .config import PROJECT_ROOT
from .db import query_df, warehouse_exists

TABLES = {
    "gold.fact_sales": ("Invoice-line sales fact (incl. cancellations & non-product lines)", "UCI Online Retail II",
                        "Silver lines + surrogate keys; incremental delete+insert on line_id", "Sales Operations"),
    "gold.fact_orders": ("Invoice-level fact with new/returning/guest classification", "UCI Online Retail II",
                         "Aggregate product lines per invoice; order_seq per customer", "Sales Operations"),
    "gold.fact_inventory": ("SYNTHETIC stock snapshot x ACTUAL trailing demand", "Synthetic generator + UCI",
                            "Join snapshot to 90/365-day real unit sales", "Supply Chain"),
    "gold.fact_marketing": ("SYNTHETIC daily campaign performance", "Synthetic generator",
                            "Derived ratios CTR, CVR, ROAS", "Marketing"),
    "gold.dim_customer": ("Customer dimension with derived attributes", "UCI Online Retail II",
                          "First purchase = registration; RFM quintile segments; revenue-percentile tiers", "CRM"),
    "gold.dim_product": ("Product master derived from sales lines", "UCI Online Retail II",
                         "Mode description; keyword-rule category; median price", "Merchandising"),
    "gold.dim_date": ("Calendar dimension", "Generated", "Daily calendar over data range + 1 year", "BI"),
    "gold.dim_geography": ("Country / region reference", "Seed country_regions.csv", "Manual mapping of source "
                           "country labels to ISO codes and regions", "BI"),
    "gold.dim_campaign": ("SYNTHETIC campaign dimension", "Synthetic generator", "Pass-through", "Marketing"),
    "gold.mart_kpi_monthly": ("Governed monthly KPIs", "gold facts", "See metric definitions", "Finance / BI"),
    "gold.mart_cohort_retention": ("Acquisition cohort retention", "gold facts", "Cohort = first purchase month",
                                   "CRM"),
    "gold.mart_product_monthly": ("Product x month performance", "gold facts", "Aggregation", "Merchandising"),
    "gold.mart_geo_monthly": ("Country x month performance", "gold facts", "Aggregation", "Sales"),
    "gold.mart_daily_sales": ("Daily sales series", "gold facts", "Aggregation", "BI"),
    "silver.stg_transactions": ("Validated, standardized lines", "Quality stage output",
                                "Rules in businesspulse/quality/cleaning.py; failing rows quarantined", "Data Engineering"),
    "bronze.brz_transactions": ("Raw ingested lines (all batches)", "Excel / CSV / Kafka", "None (raw + lineage cols)",
                                "Data Engineering"),
    "bronze.brz_quarantine_transactions": ("Rows rejected by DQ rules", "Quality stage", "dq_reasons lists failing rules",
                                           "Data Engineering"),
}

COLUMNS = {
    "line_id": "Deterministic surrogate key md5(source file|sheet|row)",
    "invoice_no": "Invoice number; prefix C = cancellation, A = bad-debt adjustment",
    "stock_code": "Product code (upper-cased)", "description": "Product description (filled from product master if missing)",
    "quantity": "Units on the line (negative for cancellations)", "unit_price": "Unit price, GBP",
    "line_revenue": "quantity x unit_price, GBP", "gross_sales": "line_revenue for non-cancelled product lines else 0",
    "returns_value": "-line_revenue for cancelled product lines else 0", "invoice_ts": "Invoice timestamp",
    "invoice_date": "Invoice date", "customer_key": "Customer surrogate key (-1 = guest/unidentified)",
    "customer_id": "Source customer number", "product_key": "md5(stock_code)", "geography_key": "md5(country label)",
    "date_key": "yyyymmdd integer", "line_type": "product / postage / fee / adjustment / discount / manual / samples / voucher",
    "is_cancellation": "True for cancellation invoices", "sales_channel": "Always 'Online' (single-channel source)",
    "batch_id": "Ingestion batch that delivered the row", "customer_type": "New / Returning / Guest",
    "rfm_segment": "Derived RFM segment", "loyalty_tier": "Derived revenue-percentile tier (not a real loyalty programme)",
    "registration_date": "Derived: first purchase date", "is_synthetic": "True when the row is generated demo data",
    "net_revenue": "Product revenue net of cancellations", "retention_rate": "Retained / previous-month active customers",
}

GLOSSARY = [
    ("Net revenue", "Product sales minus cancellations. Excludes postage, fees and manual adjustments."),
    ("Gross sales", "Product sales before cancellations."),
    ("Cancellation / return", "An invoice prefixed 'C' reversing earlier lines; the source has no separate returns system."),
    ("Order", "A non-cancelled invoice with at least one product line."),
    ("Active customer", "Identified customer with at least one order in the period."),
    ("New customer", "Customer whose first-ever order is in the period (data starts Dec 2009 - left-censored)."),
    ("Retention (MoM)", "Share of last month's active customers active again this month."),
    ("Repeat purchase rate", "Share of active customers whose order was not their first."),
    ("Churn (model)", "No purchase in the 90 days after the scoring date."),
    ("CLV (180d)", "Predicted net revenue in the next 180 days."),
    ("Guest", "Line or order without a Customer ID (customer_key = -1)."),
    ("Quarantine", "Rows failing an error-level data-quality rule; excluded from silver/gold, kept for audit."),
    ("Synthetic data", "Generated demonstration data (inventory, marketing); never real company performance."),
    ("ROAS", "Attributed revenue / marketing cost (synthetic only)."),
    ("Partial month", "Month not fully covered by data (Dec 2011 ends on the 9th)."),
]


def data_dictionary() -> pd.DataFrame:
    if not warehouse_exists():
        return pd.DataFrame()
    cols = query_df("""select table_schema || '.' || table_name as table_name, column_name, data_type, ordinal_position
                       from information_schema.columns where table_schema in ('bronze','silver','gold')
                       order by table_schema, table_name, ordinal_position""")
    rows = []
    for r in cols.itertuples():
        meta = TABLES.get(r.table_name, ("", "", "", ""))
        rows.append({"table": r.table_name, "column": r.column_name, "data_type": r.data_type,
                     "column_description": COLUMNS.get(r.column_name, ""), "table_description": meta[0],
                     "source_system": meta[1], "transformation_rule": meta[2], "business_owner": meta[3]})
    return pd.DataFrame(rows)


def glossary() -> pd.DataFrame:
    return pd.DataFrame(GLOSSARY, columns=["term", "definition"])


def write_markdown() -> str:
    dd = data_dictionary()
    lines = ["# Data dictionary", "", "Generated by `python -m businesspulse.catalog` from the live warehouse.", "",
             "## Tables", "", "| Table | Description | Source | Transformation | Owner |", "|---|---|---|---|---|"]
    lines += [f"| `{t}` | {d} | {s} | {tr} | {o} |" for t, (d, s, tr, o) in TABLES.items()]
    lines += ["", "## Columns (gold layer)", "", "| Table | Column | Type | Description |", "|---|---|---|---|"]
    for r in dd[dd.table.str.startswith("gold.")].itertuples():
        lines.append(f"| `{r.table}` | `{r.column}` | {r.data_type} | {r.column_description} |")
    lines += ["", "## Business glossary", "", "| Term | Definition |", "|---|---|"]
    lines += [f"| {t} | {d} |" for t, d in GLOSSARY]
    lines += ["", "## Metric definitions", "", "| Key | Name | Definition | Calculation | Basis |", "|---|---|---|---|---|"]
    lines += [f"| `{r.key}` | {r.name} | {r.definition} | {r.calculation} | {r.data_basis} |"
              for r in metric_catalog().itertuples()]
    path = PROJECT_ROOT / "docs" / "data_dictionary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    print(write_markdown())
