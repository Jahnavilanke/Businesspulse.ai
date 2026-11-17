from __future__ import annotations

import plotly.express as px
import streamlit as st

from businesspulse.config import settings
from dashboard import components as ui


def render() -> None:
    ui.page_header("Inventory Analytics", "Stock levels, turnover, stockouts, slow movers and demand forecast.", "mixed")
    if not ui.require_warehouse():
        return
    demand_ok = ui.has("ml", "demand_forecast")
    if not (settings.enable_synthetic_support and ui.has("gold", "fact_inventory")):
        ui.empty_state("Stock data unavailable", "No inventory source is connected (synthetic support disabled). "
                       "Demand forecasts below still use actual sales.", "🔒")
        inv = None
    else:
        ui.synthetic_banner("Stock quantities, reorder levels and lead times are generated. Demand columns "
                            "(units sold in the last 90/365 days) and forecasts come from ACTUAL sales.")
        inv = ui.q("""select i.*, p.product_name, p.category from gold.fact_inventory i
                      join gold.dim_product p using (product_key)""")
    if inv is not None:
        active = inv[inv.units_last_90d_actual > 0]
        turnover = inv.units_last_365d_actual.sum() / max(inv.stock_qty.sum(), 1)
        ui.card_row([ui.kpi_card("Units on hand", inv.stock_qty.sum(), "count", basis="synthetic"),
                     ui.kpi_card("Inventory turnover (units)", turnover, "x", basis="synthetic"),
                     ui.kpi_card("Stockout rate (active SKUs)", active.is_stockout.mean(), "ratio", basis="synthetic"),
                     ui.kpi_card("Below reorder level", active.below_reorder_level.mean(), "ratio", basis="synthetic"),
                     ui.kpi_card("Slow-moving positions", inv.is_slow_moving.mean(), "ratio", basis="synthetic",
                                 help_text="No actual sales in the last 90 days")])
        t1, t2, t3 = st.tabs(["Stock levels", "Stockout & reorder", "Slow movers"])
        with t1:
            wh = inv.groupby("warehouse_id", as_index=False)[["stock_qty"]].sum()
            cat = inv.groupby("category", as_index=False)[["stock_qty", "units_last_365d_actual"]].sum()
            cat["turnover"] = cat.units_last_365d_actual / cat.stock_qty.clip(lower=1)
            c1, c2 = st.columns(2)
            c1.plotly_chart(ui.style(px.bar(wh, x="warehouse_id", y="stock_qty", color_discrete_sequence=[ui.SERIES[0]]),
                                     "Units on hand by warehouse").update_layout(hovermode="closest"), width="stretch")
            c2.plotly_chart(ui.style(px.bar(cat.sort_values("turnover"), x="turnover", y="category", orientation="h",
                                            color_discrete_sequence=[ui.SERIES[0]]), "Turnover by category", 420)
                            .update_layout(hovermode="closest"), width="stretch")
        with t2:
            risk = active[(active.is_stockout) | (active.below_reorder_level)].sort_values("units_last_90d_actual",
                                                                                         ascending=False)
            st.markdown(f"**{len(risk):,} actively selling positions are stocked out or below reorder level.**")
            ui.searchable_table(risk[["warehouse_id", "stock_code", "product_name", "stock_qty", "reorder_level",
                                      "reorder_qty", "supplier_lead_time_days", "units_last_90d_actual", "days_of_cover"]],
                                "inv_risk", 380)
            ui.download_buttons(risk, "reorder_candidates_synthetic", "dl_inv")
        with t3:
            slow = inv[inv.is_slow_moving & (inv.stock_qty > 0)].sort_values("stock_qty", ascending=False)
            st.markdown(f"**{len(slow):,} positions hold stock but had no actual sales in the last 90 days.**")
            ui.searchable_table(slow[["warehouse_id", "stock_code", "product_name", "category", "stock_qty"]], "slow", 360)
    st.markdown("### Demand forecast (actual sales)")
    if not demand_ok:
        ui.empty_state("Forecast not trained", "Run the pipeline with ML enabled.")
        return
    fc = ui.q("""select d.stock_code, p.product_name, d.week, d.forecast_units, d.model
                 from ml.demand_forecast d join gold.dim_product p using (stock_code)""")
    hist = ui.q("""select stock_code, date_trunc('week', invoice_date) as week, sum(quantity) units from gold.fact_sales
                   where line_type='product' and not is_cancellation and invoice_date >= (select max(invoice_date) - 120
                   from gold.fact_sales) and stock_code in (select distinct stock_code from ml.demand_forecast)
                   group by 1, 2""")
    names = fc.drop_duplicates("stock_code").set_index("stock_code").product_name
    code = st.selectbox("Product", names.index, format_func=lambda c: f"{c} - {names[c]}", key="dem_prod")
    h, p = hist[hist.stock_code == code].sort_values("week"), fc[fc.stock_code == code]
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_scatter(x=h.week, y=h.units, name="Actual weekly units", mode="lines+markers", line_color=ui.SERIES[0])
    fig.add_scatter(x=p.week, y=p.forecast_units, name=f"Forecast ({p.model.iloc[0]})", mode="lines+markers",
                    line=dict(color=ui.SERIES[1], dash="dash"))
    ui.chart(ui.style(fig, "Weekly demand: actual vs 4-week forecast"), "dem_chart")
    if inv is not None:
        need = fc.groupby("stock_code", as_index=False).forecast_units.sum().merge(
            inv.groupby("stock_code", as_index=False).stock_qty.sum(), on="stock_code")
        need["projected_gap_4w"] = need.stock_qty - need.forecast_units
        short = need[need.projected_gap_4w < 0].sort_values("projected_gap_4w")
        st.markdown(f"**Inventory demand prediction:** {len(short)} of {len(need)} forecast products have less "
                    "(synthetic) stock than forecast 4-week demand.")
        st.dataframe(short.merge(names.reset_index(), on="stock_code"), hide_index=True, width="stretch")
