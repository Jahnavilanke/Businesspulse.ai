"""Root-cause investigation toolkit.

Produces *evidence*, not conclusions: decompositions, contribution rankings, segment
comparisons and anomaly flags, each with the numbers behind them. Narrative text only
restates computed values and always separates observations from hypotheses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from ..db import query_df
from . import kpis
from .statistics import zscore_anomalies

CAUSATION_CAVEAT = ("These are descriptive decompositions of observed data. They show WHERE a change occurred "
                    "(which segments moved), not WHY. Treat drivers as hypotheses to validate with the business "
                    "(e.g. pricing changes, stock availability, campaign calendar), which are not in the data.")

DIMENSIONS = {
    "country": ("f.country_raw", ""),
    "region": ("g.region", "join gold.dim_geography g using (geography_key)"),
    "category": ("p.category", "join gold.dim_product p using (product_key)"),
    "product": ("p.stock_code || ' - ' || p.product_name", "join gold.dim_product p using (product_key)"),
    "customer_segment": ("c.rfm_segment", "join gold.dim_customer c using (customer_key)"),
    "loyalty_tier": ("c.loyalty_tier", "join gold.dim_customer c using (customer_key)"),
    "customer_type": ("coalesce(o.customer_type, 'Guest')", "left join gold.fact_orders o using (invoice_no)"),
}


@dataclass
class Finding:
    observation: str
    metric: str
    current: float | None = None
    comparison: float | None = None
    kind: str = "observation"      # observation | hypothesis | data_note


@dataclass
class Investigation:
    question: str
    period: tuple[date, date]
    comparison_period: tuple[date, date]
    findings: list[Finding] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    data_basis: str = "actual"


def contribution(dim: str, cur: tuple[date, date], prev: tuple[date, date], metric: str = "net_revenue",
                 top: int = 15) -> pd.DataFrame:
    expr, join = DIMENSIONS[dim]
    value = {"net_revenue": "f.line_revenue", "returns_value": "f.returns_value", "gross_sales": "f.gross_sales"}[metric]
    df = query_df(f"""
        select {expr} as member,
               sum(case when f.invoice_date between ? and ? then {value} else 0 end) as current,
               sum(case when f.invoice_date between ? and ? then {value} else 0 end) as comparison
        from gold.fact_sales f {join}
        where f.line_type = 'product' and (f.invoice_date between ? and ? or f.invoice_date between ? and ?)
        group by 1
    """, [cur[0], cur[1], prev[0], prev[1], cur[0], cur[1], prev[0], prev[1]])
    df["change"] = df["current"] - df["comparison"]
    total_change = df["change"].sum()
    df["change_pct"] = df["change"] / df["comparison"].where(df["comparison"] != 0)
    df["share_of_total_change"] = df["change"] / total_change if total_change else None
    df["current_share"] = df["current"] / df["current"].sum() if df["current"].sum() else None
    order = df["change"].abs().sort_values(ascending=False).index
    return df.loc[order].head(top).reset_index(drop=True)


def revenue_bridge(cur: tuple[date, date], prev: tuple[date, date]) -> pd.DataFrame:
    """Net revenue change = orders effect + AOV effect - change in returns (exact, sequential)."""
    c, p = kpis.compute_kpis(*cur), kpis.compute_kpis(*prev)
    if not (c["orders"] and p["orders"]):
        return pd.DataFrame()
    orders_effect = (c["orders"] - p["orders"]) * p["avg_order_value"]
    aov_effect = (c["avg_order_value"] - p["avg_order_value"]) * c["orders"]
    returns_effect = -(c["returns_value"] - p["returns_value"])
    rows = [("Comparison net revenue", p["net_revenue"]), ("Order volume effect", orders_effect),
            ("Average order value effect", aov_effect), ("Returns / cancellations effect", returns_effect),
            ("Current net revenue", c["net_revenue"])]
    df = pd.DataFrame(rows, columns=["component", "value"])
    df.attrs.update(current=c, comparison=p)
    return df


def customer_bridge(cur: tuple[date, date], prev: tuple[date, date]) -> pd.DataFrame:
    q = """
        select coalesce(o.customer_type, 'Guest') as customer_type,
               count(distinct o.customer_key) filter (where o.customer_key <> -1) as customers,
               count(*) as orders, sum(o.order_revenue) as revenue
        from gold.fact_orders o where not o.is_cancellation and o.invoice_date between ? and ?
        group by 1
    """
    a = query_df(q, list(cur)).set_index("customer_type").add_suffix("_current")
    b = query_df(q, list(prev)).set_index("customer_type").add_suffix("_comparison")
    df = a.join(b, how="outer").fillna(0)
    df["revenue_change"] = df["revenue_current"] - df["revenue_comparison"]
    return df.reset_index()


def large_returns(cur: tuple[date, date], top: int = 10) -> pd.DataFrame:
    return query_df("""
        select f.invoice_no, f.invoice_date, f.customer_key, f.stock_code, p.product_name, f.quantity,
               f.returns_value
        from gold.fact_sales f join gold.dim_product p using (product_key)
        where f.is_cancellation and f.line_type = 'product' and f.invoice_date between ? and ?
        order by f.returns_value desc limit ?
    """, [cur[0], cur[1], top])


def daily_anomalies(cur: tuple[date, date], metric: str = "net_revenue") -> pd.DataFrame:
    df = query_df(f"select date_day, {metric} from gold.mart_daily_sales order by 1")
    df = df.set_index(pd.to_datetime(df["date_day"]))[metric].asfreq("D")
    # Days with no trading (weekends/holidays) are absent rather than zero; compare trading days only.
    trading = df.dropna()
    z = zscore_anomalies(trading, window=28, threshold=3.0)
    z = z[(z.index >= pd.Timestamp(cur[0])) & (z.index <= pd.Timestamp(cur[1]))]
    return z[z["is_anomaly"]].reset_index(names="date")


def _pct(a, b):
    return None if a is None or not b else (a - b) / abs(b)


def investigate_revenue(cur: tuple[date, date], prev: tuple[date, date]) -> Investigation:
    inv = Investigation("Why did net revenue change?", cur, prev, caveats=[CAUSATION_CAVEAT])
    bridge = revenue_bridge(cur, prev)
    if bridge.empty:
        inv.findings.append(Finding("Insufficient data in one of the periods to decompose revenue.", "net_revenue",
                                    kind="data_note"))
        return inv
    c, p = bridge.attrs["current"], bridge.attrs["comparison"]
    inv.tables["revenue_bridge"] = bridge
    ch = _pct(c["net_revenue"], p["net_revenue"])
    inv.findings.append(Finding(
        f"Net revenue was {kpis.format_value(c['net_revenue'], 'currency')} vs "
        f"{kpis.format_value(p['net_revenue'], 'currency')} ({ch:+.1%}).", "net_revenue", c["net_revenue"], p["net_revenue"]))
    comps = bridge.iloc[1:4].copy()
    comps["abs"] = comps["value"].abs()
    for _, r in comps.sort_values("abs", ascending=False).iterrows():
        inv.findings.append(Finding(f"{r.component}: {kpis.format_value(r.value, 'currency')}.", "net_revenue", r.value))
    inv.findings.append(Finding(
        f"Orders {c['orders']:,.0f} vs {p['orders']:,.0f} ({_pct(c['orders'], p['orders']):+.1%}); AOV "
        f"{kpis.format_value(c['avg_order_value'], 'currency')} vs {kpis.format_value(p['avg_order_value'], 'currency')}.",
        "orders", c["orders"], p["orders"]))
    if c["return_rate"] is not None and p["return_rate"] is not None:
        inv.findings.append(Finding(f"Return rate {c['return_rate']:.1%} vs {p['return_rate']:.1%}.", "return_rate",
                                    c["return_rate"], p["return_rate"]))
    for dim in ["country", "category", "customer_type"]:
        t = contribution(dim, cur, prev)
        inv.tables[f"contribution_by_{dim}"] = t
        if len(t) and t["change"].sum() != 0:
            neg = t.sort_values("change").iloc[0]
            pos = t.sort_values("change", ascending=False).iloc[0]
            low = "largest decline" if neg.change < 0 else "smallest increase"
            high = "largest increase" if pos.change > 0 else "smallest decline"
            inv.findings.append(Finding(
                f"By {dim.replace('_', ' ')}: {low} {neg.member} ({kpis.format_value(neg.change, 'currency')}), "
                f"{high} {pos.member} ({kpis.format_value(pos.change, 'currency')}).", f"net_revenue_by_{dim}"))
    lr = large_returns(cur)
    inv.tables["largest_cancellations"] = lr
    if len(lr) and c["returns_value"]:
        top_share = lr["returns_value"].iloc[0] / c["returns_value"]
        if top_share > 0.25:
            r0 = lr.iloc[0]
            inv.findings.append(Finding(
                f"A single cancellation line ({r0.invoice_no}, {r0.product_name}, {abs(r0.quantity):,} units) accounts "
                f"for {top_share:.0%} of returns value in the period - check whether it reverses an erroneous order.",
                "returns_value", float(r0.returns_value), kind="hypothesis"))
    an = daily_anomalies(cur)
    inv.tables["daily_anomalies"] = an
    if len(an):
        inv.findings.append(Finding(f"{len(an)} trading day(s) had net revenue more than 3 robust standard deviations "
                                    "from the trailing 28-day median.", "net_revenue_daily", float(len(an))))
    d0, d1 = kpis.data_range()
    if cur[1] >= d1 and pd.Timestamp(d1) < pd.Timestamp(d1).to_period("M").to_timestamp("M"):
        inv.caveats.append(f"Data ends {d1}; the latest month is partial, so month totals are not comparable to full months.")
    return inv


def investigate_retention(cur: tuple[date, date], prev: tuple[date, date]) -> Investigation:
    inv = Investigation("Why did customer retention change?", cur, prev, caveats=[CAUSATION_CAVEAT])
    m = kpis.monthly_kpis()
    m["month"] = pd.to_datetime(m["month"])
    sel = lambda a, b: m[(m.month >= pd.Timestamp(a).to_period("M").to_timestamp()) & (m.month <= pd.Timestamp(b))]
    c, p = sel(*cur), sel(*prev)
    rc, rp = c["retention_rate"].mean(), p["retention_rate"].mean()
    inv.findings.append(Finding(f"Average month-over-month retention {rc:.1%} vs {rp:.1%}.", "retention_rate", rc, rp))
    inv.findings.append(Finding(
        f"Active customers per month averaged {c.active_customers.mean():,.0f} vs {p.active_customers.mean():,.0f}; "
        f"new customers per month {c.new_customers.mean():,.0f} vs {p.new_customers.mean():,.0f}.", "active_customers"))
    inv.tables["monthly_retention"] = pd.concat([p.assign(period="comparison"), c.assign(period="current")])[
        ["year_month", "period", "active_customers", "new_customers", "retained_customers", "retention_rate"]]
    seg = contribution("customer_segment", cur, prev)
    inv.tables["revenue_by_rfm_segment"] = seg
    cb = customer_bridge(cur, prev)
    inv.tables["customer_type_bridge"] = cb
    if any(c["is_partial_month"]):
        inv.caveats.append("The current window includes a partial month, which mechanically lowers retention.")
    inv.caveats.append("RFM segments are assigned as of the last data date, so they describe customers today, "
                       "not at the time of the earlier period.")
    return inv


def investigate_products(cur: tuple[date, date], prev: tuple[date, date]) -> Investigation:
    inv = Investigation("Which products / categories are underperforming?", cur, prev, caveats=[CAUSATION_CAVEAT])
    cat = contribution("category", cur, prev, top=20)
    prod = contribution("product", cur, prev, top=40)
    inv.tables["category_contribution"] = cat
    under = prod[(prod["comparison"] > 1000) & (prod["change"] < 0)].sort_values("change").head(15)
    inv.tables["underperforming_products"] = under
    for _, r in cat.sort_values("change").head(3).iterrows():
        if r.change < 0:
            inv.findings.append(Finding(f"Category {r.member}: {kpis.format_value(r.change, 'currency')} "
                                        f"({r.change_pct:+.1%} vs comparison).", "net_revenue_by_category", r.current, r.comparison))
    if len(under):
        inv.findings.append(Finding(f"{len(under)} products with >£1,000 comparison-period revenue declined; largest: "
                                    f"{under.iloc[0].member} ({kpis.format_value(under.iloc[0].change, 'currency')}).",
                                    "net_revenue_by_product"))
    inv.caveats.append("Categories are derived from product descriptions by keyword rules (~20% unclassified).")
    inv.caveats.append("No stock or pricing history exists, so availability or price changes cannot be ruled in or out.")
    return inv


def investigate_marketing(cur: tuple[date, date], prev: tuple[date, date]) -> Investigation:
    inv = Investigation("Why did marketing ROI change?", cur, prev, data_basis="synthetic",
                        caveats=["SYNTHETIC DATA: campaign data is generated for demonstration. Findings illustrate the "
                                 "method and do not describe the retailer's real marketing performance.",
                                 CAUSATION_CAVEAT])
    if not kpis.synthetic_available():
        inv.findings.append(Finding("Marketing data is not available (synthetic support disabled).", "roas", kind="data_note"))
        return inv
    q = """select channel, sum(cost) cost, sum(clicks) clicks, sum(conversions) conversions,
                  sum(attributed_revenue) revenue from gold.fact_marketing where activity_date between ? and ? group by 1"""
    a = query_df(q, list(cur)).set_index("channel").add_suffix("_current")
    b = query_df(q, list(prev)).set_index("channel").add_suffix("_comparison")
    df = a.join(b, how="outer").fillna(0)
    for s in ["current", "comparison"]:
        df[f"roas_{s}"] = df[f"revenue_{s}"] / df[f"cost_{s}"].where(df[f"cost_{s}"] > 0)
        df[f"cvr_{s}"] = df[f"conversions_{s}"] / df[f"clicks_{s}"].where(df[f"clicks_{s}"] > 0)
    df["roas_change"] = df["roas_current"] - df["roas_comparison"]
    inv.tables["channel_comparison"] = df.reset_index()
    tot_c = df["revenue_current"].sum() / df["cost_current"].sum()
    tot_p = df["revenue_comparison"].sum() / df["cost_comparison"].sum()
    inv.findings.append(Finding(f"Blended ROAS {tot_c:.2f}x vs {tot_p:.2f}x (synthetic).", "roas", tot_c, tot_p))
    w = df.sort_values("roas_change").iloc[0]
    inv.findings.append(Finding(f"Largest ROAS decline: {w.name} ({w.roas_current:.2f}x vs {w.roas_comparison:.2f}x); "
                                f"spend {kpis.format_value(w.cost_current, 'currency')} vs "
                                f"{kpis.format_value(w.cost_comparison, 'currency')}.", "roas_by_channel"))
    return inv


INVESTIGATIONS = {
    "Why did revenue decline?": investigate_revenue,
    "Why did customer retention decrease?": investigate_retention,
    "Why did marketing ROI change?": investigate_marketing,
    "Why are certain products underperforming?": investigate_products,
}
