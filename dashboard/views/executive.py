from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from businesspulse.analytics.kpis import METRICS
from dashboard import components as ui


@st.cache_data(show_spinner=False, ttl=600)
def _latest_insight(version: float) -> dict:
    from businesspulse.insights.engine import revenue_change
    ins = revenue_change()
    return {"period": ins.reporting_period, "comparison": ins.comparison_period, "observations": ins.observations}


def render() -> None:
    ui.page_header("Executive Dashboard", "Company performance at a glance - governed KPIs from the gold layer.", "actual")
    if not ui.require_warehouse():
        return
    f = ui.filters()
    start, end = f["start"], f["end"]
    kind = st.radio("Compare with", ["Previous period", "Same period last year"], horizontal=True, key="exec_cmp")
    cmp = ui.kpi_cmp(start, end, "prior" if kind == "Previous period" else "yoy").set_index("kpi")
    st.caption(f"Reporting period **{start} → {end}** · comparison {cmp.iloc[0]['comparison_period']}"
               + (" · Country/category filters apply on the Sales page" if f["countries"] or f["categories"] else ""))

    def card(k):
        r = cmp.loc[k]
        return ui.kpi_card(r["name"], r["current"], r["unit"], r["change_pct"], r["direction"], r["data_basis"],
                           METRICS[k].definition)
    ui.card_row([card(k) for k in ["net_revenue", "orders", "avg_order_value", "active_customers", "new_customers",
                                   "retention_rate"]])
    ui.card_row([card(k) for k in ["gross_sales", "return_rate", "repeat_purchase_rate", "units_sold", "gross_profit"]])
    st.caption("Gross profit is unavailable: the source data has no product cost. The simulator can model it from an "
               "explicit margin assumption.")
    with st.expander("🧭 What changed in the latest full month? (grounded summary)", expanded=True):
        ins = _latest_insight(ui.db.warehouse_version())
        st.markdown(f"**{ins['period']}** vs {ins['comparison']}")
        for o in ins["observations"][:5]:
            st.markdown(f"- {o}")
        st.caption("Generated from warehouse queries by the rule-based insight engine - open AI Insights or the "
                   "BA Root-cause workspace for the full evidence and caveats.")

    m = ui.q("select * from gold.mart_kpi_monthly order by month")
    m["month"] = pd.to_datetime(m["month"])
    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        full, part = m[~m.is_partial_month], m[m.is_partial_month]
        fig.add_bar(x=full.month, y=full.net_revenue, name="Net revenue", marker_color=ui.SERIES[0],
                    hovertemplate="%{x|%b %Y}: £%{y:,.0f}<extra></extra>")
        if len(part):
            fig.add_bar(x=part.month, y=part.net_revenue, name="Partial month", marker_color="#b9d3f5",
                        hovertemplate="%{x|%b %Y} (partial): £%{y:,.0f}<extra></extra>")
        fig.update_layout(yaxis_tickprefix="£", yaxis_tickformat="~s", hovermode="closest")
        ui.chart(ui.style(fig, "Monthly net revenue"), "exec_rev")
    with c2:
        fig = go.Figure()
        fig.add_scatter(x=m.month, y=m.new_customers, name="New customers", mode="lines+markers",
                        line_color=ui.SERIES[0])
        fig.add_scatter(x=m.month, y=m.returning_customers, name="Returning customers", mode="lines+markers",
                        line_color=ui.SERIES[1])
        ui.chart(ui.style(fig, "Customer growth"), "exec_cust")

    c3, c4 = st.columns(2)
    with c3:
        fig = go.Figure(go.Scatter(x=m.month, y=m.retention_rate, mode="lines+markers", name="Retention",
                                   line_color=ui.SERIES[2], hovertemplate="%{x|%b %Y}: %{y:.1%}<extra></extra>"))
        fig.update_layout(yaxis_tickformat=".0%")
        ui.chart(ui.style(fig, "Month-over-month customer retention"), "exec_ret")
    with c4:
        from businesspulse.db import app_query
        reg =app_query("select kpi_id, name, target_value, direction, unit, data_basis, department from kpi_registry "
                        "where target_value is not null")
        if f.get("department") and f["department"] != "All":
            reg = reg[reg.department == f["department"]]
        vals = ui.kpi_values(start, end)
        months = max(1, round((pd.Timestamp(end) - pd.Timestamp(start)).days / 30.4))
        rows = []
        for r in reg.itertuples():
            a = vals.get(r.kpi_id)
            if a is None:
                continue
            tgt = r.target_value * months if r.kpi_id in {"net_revenue", "orders", "new_customers"} else r.target_value
            ok = a >= tgt if r.direction == "higher_is_better" else a <= tgt
            rows.append({"KPI": r.name + (" (synthetic)" if r.data_basis == "synthetic" else ""),
                         "Actual": a, "Target": tgt, "Attainment": a / tgt if tgt else None,
                         "Status": "✅ On target" if ok else "⚠️ Below target"})
        st.markdown("**KPI performance vs target**")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
                "Actual": st.column_config.NumberColumn(format="%.4g"),
                "Target": st.column_config.NumberColumn(format="%.4g"),
                "Attainment": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1.5)})
            st.caption(f"Volume targets (revenue, orders, new customers) are monthly targets × {months} months in range. "
                       "Targets are maintained in the BA KPI registry.")
        else:
            ui.empty_state("No targets", "Set targets in the Business Analyst workspace › KPI registry.")
