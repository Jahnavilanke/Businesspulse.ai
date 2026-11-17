"""Report generation from processed warehouse data (CSV / Excel / Markdown executive summary)."""
from __future__ import annotations

import io
from datetime import date, datetime

import pandas as pd

from ..analytics import kpis
from ..business_analysis.store import list_records
from ..db import app_query, query_df
from ..insights import engine


def to_excel(sheets: dict[str, pd.DataFrame], meta: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        info = pd.DataFrame(list({"Generated": datetime.now().isoformat(timespec="seconds"),
                                  "Source": "BusinessPulse AI warehouse (gold layer)",
                                  "Primary data": "UCI Online Retail II (CC BY 4.0) - real transactions",
                                  "Synthetic data": "Inventory & marketing sheets marked data_basis=synthetic are "
                                                    "demonstration data", **(meta or {})}.items()),
                            columns=["Field", "Value"])
        info.to_excel(xw, sheet_name="About", index=False)
        for name, df in sheets.items():
            out = df.copy()
            for c in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[c]) and getattr(out[c].dt, "tz", None) is not None:
                    out[c] = out[c].dt.tz_localize(None)
            out.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            for i, col in enumerate(out.columns, start=1):
                width = min(60, max(10, len(str(col)) + 2, *(len(str(v)) + 2 for v in out[col].head(50))))
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
    return buf.getvalue()


def kpi_report(start: date, end: date) -> dict[str, pd.DataFrame]:
    cmp_prior = kpis.kpi_comparison(start, end, "prior")
    cmp_yoy = kpis.kpi_comparison(start, end, "yoy")
    return {"KPIs vs prior period": cmp_prior, "KPIs vs last year": cmp_yoy, "Monthly KPIs": kpis.monthly_kpis(),
            "Metric definitions": kpis.metric_catalog()}


def sales_report(start: date, end: date) -> dict[str, pd.DataFrame]:
    p = [start, end]
    return {
        "Daily sales": query_df("select * from gold.mart_daily_sales where date_day between ? and ?", p),
        "By country": query_df("""select country_name, region, sum(net_revenue) net_revenue, sum(orders) orders
                                  from gold.mart_geo_monthly where month between date_trunc('month', ?::date) and ?
                                  group by all order by net_revenue desc""", p),
        "By category": query_df("""select category, sum(net_revenue) net_revenue, sum(units_sold) units
                                   from gold.mart_product_monthly where month between date_trunc('month', ?::date) and ?
                                   group by 1 order by 2 desc""", p),
        "Top products": query_df("""select stock_code, product_name, category, sum(net_revenue) net_revenue,
                                    sum(units_sold) units from gold.mart_product_monthly
                                    where month between date_trunc('month', ?::date) and ? group by all
                                    order by net_revenue desc limit 200""", p),
    }


def customer_report() -> dict[str, pd.DataFrame]:
    out = {"RFM segments": query_df("""select rfm_segment, loyalty_tier, count(*) customers,
                                       sum(lifetime_net_revenue) revenue from gold.dim_customer
                                       where customer_key <> -1 group by all order by revenue desc"""),
           "Cohort retention": query_df("select * from gold.mart_cohort_retention")}
    try:
        out["Churn risk"] = query_df("""select s.customer_key, c.home_country, c.rfm_segment, s.churn_probability,
                                       s.risk_band, s.top_drivers, c.lifetime_net_revenue
                                       from ml.churn_scores s join gold.dim_customer c using (customer_key)
                                       order by churn_probability desc""")
        out["Predicted value 180d"] = query_df("select * from ml.clv_scores order by predicted_value_180d desc")
    except Exception:
        pass
    return out


def data_quality_report() -> dict[str, pd.DataFrame]:
    return {"Latest DQ results": query_df("""select * from ops.dq_results where run_id =
                                            (select run_id from ops.dq_scores order by checked_at desc limit 1)"""),
            "DQ score history": query_df("select * from ops.dq_scores order by checked_at"),
            "Quarantine summary": query_df("""select dq_reasons, count(*) rows from {q} group by 1 order by 2 desc"""
                                           .format(q="bronze.brz_quarantine_transactions")),
            "Ingestion batches": query_df("select * from ops.ingestion_batches order by started_at")}


def ba_report() -> dict[str, pd.DataFrame]:
    return {"Requirements": list_records("requirements"), "KPI registry": list_records("kpi_registry"),
            "Processes": list_records("processes"), "Improvements": list_records("improvements"),
            "Decision register": list_records("decisions"),
            "Alerts": app_query("select * from alerts order by detection_date desc")}


def executive_summary(start: date, end: date) -> str:
    cmp = kpis.kpi_comparison(start, end, "yoy")
    lines = [f"# BusinessPulse AI - Executive Summary", f"Period: {start} to {end} (compared with same period last year)",
             f"Generated: {datetime.now():%Y-%m-%d %H:%M}", "",
             "Primary data: UCI Online Retail II (real transactions, CC BY 4.0). Items marked [synthetic] use "
             "demonstration data and are not real performance.", "", "## Key performance indicators", "",
             "| KPI | Current | Last year | Change |", "|---|---|---|---|"]
    for r in cmp.itertuples():
        if r.data_basis == "unavailable":
            lines.append(f"| {r.name} | unavailable (no source data) | | |")
            continue
        tag = " [synthetic]" if r.data_basis == "synthetic" else ""
        ch = f"{r.change_pct:+.1%}" if r.change_pct == r.change_pct and r.change_pct is not None else "n/a"
        lines.append(f"| {r.name}{tag} | {kpis.format_value(r.current, r.unit)} | "
                     f"{kpis.format_value(r.comparison, r.unit)} | {ch} |")
    ins = engine.answer("Why did revenue decline this month?")
    lines += ["", "## Latest-month revenue analysis", "", ins.narrative]
    al = app_query("""select severity, metric, period, data_basis, status from alerts
                      where status in ('Open','Acknowledged','Investigating') order by detection_date desc limit 10""")
    lines += ["", "## Open alerts (latest 10)", ""]
    lines += [f"- [{r.severity}] {r.metric} {r.period} ({r.data_basis}) - {r.status}" for r in al.itertuples()] or ["- none"]
    dec = app_query("select decision_id, problem, status, owner from decisions order by updated_at desc limit 10")
    lines += ["", "## Decision register", ""]
    lines += [f"- {r.decision_id}: {r.problem} - {r.status} ({r.owner})" for r in dec.itertuples()] or ["- none"]
    return "\n".join(lines)
