"""Governed KPI / metric layer.

Every KPI has a precise definition, calculation, unit, owner and *data basis*:
  * ``actual``      - computed from real UCI transactions
  * ``synthetic``   - computed from clearly-labelled synthetic demo data
  * ``unavailable`` - cannot be computed from the available sources (returns None)

All values are computed from the gold warehouse at request time - nothing is hardcoded.
SQL expressions mirror the dbt macros in ``dbt/macros/metrics.sql``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import pandas as pd

from ..db import query_df, table_exists


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    definition: str
    calculation: str
    unit: str                 # currency | count | ratio | days | x
    direction: str            # higher_is_better | lower_is_better
    department: str
    frequency: str
    data_basis: str           # actual | synthetic | unavailable


METRICS: dict[str, Metric] = {m.key: m for m in [
    Metric("net_revenue", "Net revenue", "Product sales net of cancellations/returns, GBP.",
           "SUM(line_revenue) over product lines incl. cancellation lines (negative).", "currency",
           "higher_is_better", "Sales", "Monthly", "actual"),
    Metric("gross_sales", "Gross sales", "Product sales before cancellations, GBP.",
           "SUM(line_revenue) over non-cancelled product lines.", "currency", "higher_is_better", "Sales", "Monthly", "actual"),
    Metric("returns_value", "Returns / cancellations value", "Value of cancelled product lines, GBP.",
           "-SUM(line_revenue) over cancellation product lines.", "currency", "lower_is_better", "Operations", "Weekly", "actual"),
    Metric("return_rate", "Return rate", "Share of gross sales value later cancelled.",
           "returns_value / gross_sales.", "ratio", "lower_is_better", "Operations", "Weekly", "actual"),
    Metric("orders", "Total orders", "Distinct non-cancelled invoices containing products.",
           "COUNT(DISTINCT invoice_no) where not cancellation and product line.", "count", "higher_is_better", "Sales",
           "Monthly", "actual"),
    Metric("avg_order_value", "Average order value", "Gross sales per order, GBP.", "gross_sales / orders.",
           "currency", "higher_is_better", "Sales", "Monthly", "actual"),
    Metric("units_sold", "Units sold", "Units on non-cancelled product lines.", "SUM(quantity).", "count",
           "higher_is_better", "Sales", "Monthly", "actual"),
    Metric("active_customers", "Active customers", "Identified customers with >=1 non-cancelled order in period.",
           "COUNT(DISTINCT customer_key) excluding guests (-1).", "count", "higher_is_better", "Marketing", "Monthly", "actual"),
    Metric("new_customers", "Customer acquisition (new customers)", "Customers whose first-ever order falls in the period.",
           "COUNT(DISTINCT customer_key) where order_seq = 1.", "count", "higher_is_better", "Marketing", "Monthly", "actual"),
    Metric("repeat_purchase_rate", "Repeat purchase rate",
           "Share of active customers in the period who had purchased before the period's order.",
           "returning customers / active customers.", "ratio", "higher_is_better", "CRM", "Monthly", "actual"),
    Metric("retention_rate", "Customer retention (month-over-month)",
           "Share of customers active in month m-1 who are active again in month m (average over period months).",
           "retained_customers / prev_active_customers, averaged.", "ratio", "higher_is_better", "CRM", "Monthly", "actual"),
    Metric("gross_profit", "Gross profit", "Revenue minus cost of goods sold.",
           "Requires unit cost - not present in the source data.", "currency", "higher_is_better", "Finance", "Monthly",
           "unavailable"),
    Metric("inventory_turnover", "Inventory turnover (units, annualised)",
           "Actual units sold in last 365 days / units on hand in the SYNTHETIC snapshot.",
           "SUM(units_last_365d_actual) / SUM(stock_qty).", "x", "higher_is_better", "Supply Chain", "Monthly", "synthetic"),
    Metric("stockout_rate", "Stockout rate", "Share of product-warehouse positions with zero stock (SYNTHETIC snapshot).",
           "COUNT(stock_qty = 0) / COUNT(*).", "ratio", "lower_is_better", "Supply Chain", "Weekly", "synthetic"),
    Metric("marketing_conversion_rate", "Marketing conversion rate", "Conversions / clicks (SYNTHETIC campaigns).",
           "SUM(conversions) / SUM(clicks).", "ratio", "higher_is_better", "Marketing", "Weekly", "synthetic"),
    Metric("roas", "Return on advertising spend", "Attributed revenue / campaign cost (SYNTHETIC campaigns).",
           "SUM(attributed_revenue) / SUM(cost).", "x", "higher_is_better", "Marketing", "Weekly", "synthetic"),
    Metric("cac", "Customer acquisition cost (campaign)", "Campaign cost / conversions (SYNTHETIC campaigns).",
           "SUM(cost) / SUM(conversions).", "currency", "lower_is_better", "Marketing", "Monthly", "synthetic"),
]}


def metric_catalog() -> pd.DataFrame:
    return pd.DataFrame([asdict(m) for m in METRICS.values()])


def data_range() -> tuple[date, date]:
    df = query_df("select min(invoice_date) as d0, max(invoice_date) as d1 from gold.fact_sales")
    return pd.Timestamp(df.d0[0]).date(), pd.Timestamp(df.d1[0]).date()


def synthetic_available() -> bool:
    return table_exists("gold", "fact_marketing") and table_exists("gold", "fact_inventory")


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


def compute_kpis(start: date, end: date, countries: list[str] | None = None,
                 categories: list[str] | None = None) -> dict[str, float | None]:
    """Compute all KPIs for an inclusive date window with optional filters."""
    where = ["f.invoice_date between ? and ?"]
    params: list = [start, end]
    if countries:
        where.append(f"f.country_raw in ({','.join('?' * len(countries))})")
        params += countries
    if categories:
        where.append(f"p.category in ({','.join('?' * len(categories))})")
        params += categories
    w = " and ".join(where)
    s = query_df(f"""
        select
            sum(f.line_revenue) filter (where f.line_type = 'product') as net_revenue,
            sum(f.gross_sales) as gross_sales,
            sum(f.returns_value) as returns_value,
            count(distinct f.invoice_no) filter (where not f.is_cancellation and f.line_type = 'product') as orders,
            sum(f.quantity) filter (where f.line_type = 'product' and not f.is_cancellation) as units_sold,
            count(distinct f.customer_key) filter (where f.customer_key <> -1 and not f.is_cancellation
                                                    and f.line_type = 'product') as active_customers
        from gold.fact_sales f join gold.dim_product p using (product_key)
        where {w}
    """, params).iloc[0]
    c = query_df(f"""
        select
            count(distinct o.customer_key) filter (where o.customer_type = 'New') as new_customers,
            count(distinct o.customer_key) filter (where o.customer_type = 'Returning') as returning_customers
        from gold.fact_orders o
        where o.invoice_no in (
            select distinct f.invoice_no from gold.fact_sales f join gold.dim_product p using (product_key) where {w})
    """, params).iloc[0]
    out: dict[str, float | None] = {k: _f(s[k]) for k in
                                    ["net_revenue", "gross_sales", "returns_value", "orders", "units_sold", "active_customers"]}
    out["new_customers"] = _f(c.new_customers)
    out["return_rate"] = out["returns_value"] / out["gross_sales"] if out["gross_sales"] else None
    out["avg_order_value"] = out["gross_sales"] / out["orders"] if out["orders"] else None
    out["repeat_purchase_rate"] = (_f(c.returning_customers) / out["active_customers"]) if out["active_customers"] else None
    if not countries and not categories:
        r = query_df("""select avg(retention_rate) as r from gold.mart_kpi_monthly
                        where month between date_trunc('month', ?::date) and ? and retention_rate is not null""",
                     [start, end]).iloc[0]
        out["retention_rate"] = _f(r.r)
    else:
        out["retention_rate"] = None   # retention is governed at company level only
    out["gross_profit"] = None        # unavailable: no cost data
    if synthetic_available():
        inv = query_df("""select sum(units_last_365d_actual) * 1.0 / nullif(sum(stock_qty), 0) as turnover,
                                 avg(case when is_stockout then 1.0 else 0.0 end) as stockout_rate
                          from gold.fact_inventory""").iloc[0]
        mk = query_df("""select sum(conversions) * 1.0 / nullif(sum(clicks), 0) as cvr,
                                sum(attributed_revenue) / nullif(sum(cost), 0) as roas,
                                sum(cost) / nullif(sum(conversions), 0) as cac
                         from gold.fact_marketing where activity_date between ? and ?""", [start, end]).iloc[0]
        out.update(inventory_turnover=_f(inv.turnover), stockout_rate=_f(inv.stockout_rate),
                   marketing_conversion_rate=_f(mk.cvr), roas=_f(mk.roas), cac=_f(mk.cac))
    else:
        out.update(inventory_turnover=None, stockout_rate=None, marketing_conversion_rate=None, roas=None, cac=None)
    return out


def shift_period(start: date, end: date, kind: str) -> tuple[date, date]:
    """Comparison window: previous period of equal length ('prior') or same period last year ('yoy')."""
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if kind == "yoy":
        return (s - pd.DateOffset(years=1)).date(), (e - pd.DateOffset(years=1)).date()
    length = (e - s).days + 1
    return (s - pd.Timedelta(days=length)).date(), (s - pd.Timedelta(days=1)).date()


def kpi_comparison(start: date, end: date, kind: str = "prior", **filters) -> pd.DataFrame:
    cur = compute_kpis(start, end, **filters)
    ps, pe = shift_period(start, end, kind)
    d0, _ = data_range()
    prev = compute_kpis(ps, pe, **filters) if pd.Timestamp(ps).date() >= d0 else {k: None for k in cur}
    rows = []
    snapshot_only = {"inventory_turnover", "stockout_rate"}   # single synthetic snapshot -> no comparison
    for key, m in METRICS.items():
        c, p = cur.get(key), (None if key in snapshot_only else prev.get(key))
        change = (c - p) / abs(p) if c is not None and p not in (None, 0) else None
        rows.append({"kpi": key, "name": m.name, "unit": m.unit, "data_basis": m.data_basis, "current": c,
                     "comparison": p, "change_pct": change, "direction": m.direction,
                     "comparison_period": f"{ps} to {pe}"})
    return pd.DataFrame(rows)


def monthly_kpis() -> pd.DataFrame:
    return query_df("select * from gold.mart_kpi_monthly order by month")


def format_value(value, unit: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if unit == "currency":
        sign = "-" if value < 0 else ""
        return f"{sign}£{abs(value):,.0f}" if abs(value) >= 100 else f"{sign}£{abs(value):,.2f}"
    if unit == "ratio":
        return f"{value:.1%}"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "days":
        return f"{value:,.1f} d"
    return f"{value:,.0f}"
