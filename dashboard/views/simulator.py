from __future__ import annotations

import contextlib
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from businesspulse.business_analysis.store import audit
from businesspulse.db import app_db, app_query
from businesspulse.security import can
from businesspulse.simulator.engine import Assumptions, baseline, explain, simulate, to_dict
from dashboard import components as ui


@st.cache_data(ttl=600, show_spinner=False)
def _base(v):
    return baseline()


def render() -> None:
    ui.page_header("Business Impact Simulator", "Model what-if scenarios from an actual baseline. Results are "
                   "estimates under explicit assumptions - not actual outcomes.", "mixed")
    if not ui.require_warehouse():
        return
    from businesspulse.db import warehouse_version
    b = _base(warehouse_version())
    st.caption(f"Baseline: ACTUAL trailing 12 months ({b['period']}).")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Pricing & demand**")
        price = st.slider("Price change %", -20.0, 20.0, 0.0, 0.5)
        elast = st.slider("Assumed price elasticity", -3.0, 0.0, -1.2, 0.1,
                          help="% change in volume per +1% price. Assumption - not estimated from data.")
        disc = st.slider("Additional discount %", 0.0, 30.0, 0.0, 0.5)
        lift = st.slider("Volume lift per 1% discount (%)", 0.0, 4.0, 1.5, 0.1)
        vol = st.slider("Exogenous sales volume change %", -30.0, 30.0, 0.0, 1.0)
    with c2:
        st.markdown("**Marketing & customers**")
        mkt = st.number_input("Marketing budget change (£ / year)", -200000.0, 500000.0, 0.0, 5000.0)
        roas = st.slider("Incremental ROAS assumption", 0.0, 10.0, 2.0, 0.1,
                         help="Revenue per extra £1 of marketing (no real marketing data exists).")
        ret = st.slider("Retention change (percentage points)", -10.0, 10.0, 0.0, 0.5)
    with c3:
        st.markdown("**Profit & inventory**")
        use_margin = st.checkbox("Model gross profit with an assumed margin", value=False)
        margin = st.slider("Assumed gross margin %", 10.0, 80.0, 50.0, 1.0, disabled=not use_margin)
        reorder = st.slider("Reorder quantity change % (synthetic inventory)", -50.0, 100.0, 0.0, 5.0)
    a = Assumptions(price_change_pct=price, price_elasticity=elast, discount_pct=disc, discount_volume_lift=lift,
                    volume_change_pct=vol, marketing_budget_change=mkt, incremental_roas=roas, retention_change_pp=ret,
                    gross_margin_pct=margin if use_margin else None, reorder_qty_change_pct=reorder)
    r = simulate(a, b)
    ui.card_row([ui.kpi_card("Modelled net revenue", r["net_revenue"], "currency", r["change_pct"]),
                 ui.kpi_card("Change vs baseline", r["change_vs_baseline"], "currency"),
                 ui.kpi_card("Modelled orders", r["orders"], "count", r["orders"] / b["orders"] - 1),
                 ui.kpi_card("Retention rate", r["retention_rate"], "ratio"),
                 ui.kpi_card("Gross profit", r.get("gross_profit"), "currency",
                             (r["gross_profit"] / r["baseline_gross_profit"] - 1) if "gross_profit" in r else None,
                             basis="actual" if "gross_profit" in r else "unavailable")])
    x = ["Baseline", "Pricing & volume", "Retention", "Marketing", "Scenario"]
    y = [b["net_revenue"], r["revenue_from_pricing_volume"], r["revenue_from_retention"], r["revenue_from_marketing"],
         r["net_revenue"]]
    fig = go.Figure(go.Waterfall(x=x, y=y, measure=["absolute", "relative", "relative", "relative", "total"],
                                 increasing={"marker": {"color": ui.DIVERGING[4]}},
                                 decreasing={"marker": {"color": ui.DIVERGING[0]}},
                                 totals={"marker": {"color": ui.SERIES[0]}}, connector={"line": {"color": ui.GRID}}))
    fig.update_layout(yaxis_tickprefix="£", yaxis_tickformat="~s", hovermode="closest")
    ui.chart(ui.style(fig, "Modelled annual net revenue bridge", 340), "sim_bridge")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Assumptions & caveats**")
        for n in explain(a):
            st.markdown(f"- {n}")
    with c2:
        st.markdown("**Secondary effects**")
        rows = []
        if "marketing_roas" in r:
            rows.append(("Blended ROAS (synthetic basis)", f"{r['baseline_marketing_roas']:.2f}x", f"{r['marketing_roas']:.2f}x"))
        if "days_of_cover" in r:
            rows.append(("Days of stock cover after one cycle (synthetic)", f"{r['baseline_days_of_cover']:.0f}",
                         f"{r['days_of_cover']:.0f}"))
        rows.append(("Units sold", f"{b['units_sold']:,.0f}", f"{r['units_sold']:,.0f}"))
        st.dataframe(pd.DataFrame(rows, columns=["Measure", "Baseline", "Scenario"]), hide_index=True, width="stretch")
    u = ui.user()
    st.divider()
    st.markdown("### Scenario comparison")
    if can(u.role, "save_scenarios"):
        name = st.text_input("Scenario name", placeholder="e.g. +3% price, +£20k search")
        if st.button("💾 Save scenario", type="primary") and name:
            with contextlib.closing(app_db()) as con:
                con.execute("insert into scenarios (name, created_by, assumptions_json, results_json) values (?,?,?,?)",
                            [name, u.username, json.dumps(to_dict(a)), json.dumps(r, default=float)])
                con.commit()
            audit(u.username, u.role, "save_scenario", "scenarios", name)
            st.success("Scenario saved")
    sc = app_query("select * from scenarios order by scenario_id desc")
    if sc.empty:
        ui.empty_state("No saved scenarios", "Save scenarios to compare them side by side.")
        return
    rows = []
    for s in sc.itertuples():
        res, ass = json.loads(s.results_json), json.loads(s.assumptions_json)
        rows.append({"scenario": s.name, "by": s.created_by, "net_revenue": res["net_revenue"],
                     "change_pct": res["change_pct"], "gross_profit": res.get("gross_profit"),
                     "price_change_pct": ass["price_change_pct"], "elasticity": ass["price_elasticity"],
                     "discount_pct": ass["discount_pct"], "marketing_change": ass["marketing_budget_change"],
                     "retention_pp": ass["retention_change_pp"]})
    cmp = pd.DataFrame(rows)
    st.dataframe(cmp, hide_index=True, width="stretch", column_config={
        "net_revenue": st.column_config.NumberColumn(format="£%.0f"),
        "change_pct": st.column_config.NumberColumn(format="percent"),
        "gross_profit": st.column_config.NumberColumn(format="£%.0f")})
    fig = go.Figure(go.Bar(x=cmp.scenario, y=cmp.change_pct, marker_color=ui.SERIES[0]))
    fig.update_layout(yaxis_tickformat=".1%", hovermode="closest")
    ui.chart(ui.style(fig, "Modelled revenue change by scenario", 280), "sim_cmp")
    ui.download_buttons(cmp, "scenario_comparison", "sim_dl")
