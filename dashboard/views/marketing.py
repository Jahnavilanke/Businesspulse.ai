from __future__ import annotations

import plotly.express as px
import streamlit as st

from businesspulse.config import settings
from dashboard import components as ui


def render() -> None:
    ui.page_header("Marketing Analytics", "Campaign spend, conversions, CAC, ROAS and channel performance.", "synthetic")
    if not ui.require_warehouse():
        return
    if not (settings.enable_synthetic_support and ui.has("gold", "fact_marketing")):
        ui.empty_state("Marketing module disabled",
                       "No real marketing data exists for this retailer and synthetic support is off "
                       "(BP_ENABLE_SYNTHETIC=false). Connect a marketing source to enable this page.", "🔒")
        return
    ui.synthetic_banner("Campaign data is generated and not linked to real orders, so ROAS/CAC here are method demos.")
    f = ui.filters()
    d = ui.q("""select activity_date, channel, campaign_id, cost, impressions, clicks, conversions, attributed_revenue
                from gold.fact_marketing where activity_date between ? and ?""", [f["start"], f["end"]])
    if d.empty:
        ui.empty_state("No campaigns in range", "Widen the date range.")
        return
    tot = d[["cost", "impressions", "clicks", "conversions", "attributed_revenue"]].sum()
    ui.card_row([ui.kpi_card("Spend", tot.cost, "currency", basis="synthetic"),
                 ui.kpi_card("Conversions", tot.conversions, "count", basis="synthetic"),
                 ui.kpi_card("Conversion rate", tot.conversions / tot.clicks, "ratio", basis="synthetic"),
                 ui.kpi_card("CAC (cost / conversion)", tot.cost / tot.conversions, "currency", basis="synthetic"),
                 ui.kpi_card("ROAS", tot.attributed_revenue / tot.cost, "x", basis="synthetic")])
    ch = d.groupby("channel", as_index=False)[["cost", "impressions", "clicks", "conversions", "attributed_revenue"]].sum()
    ch["ctr"] = ch.clicks / ch.impressions
    ch["conversion_rate"] = ch.conversions / ch.clicks
    ch["cac"] = ch.cost / ch.conversions
    ch["roas"] = ch.attributed_revenue / ch.cost
    order = ["Paid Search", "Paid Social", "Email", "Display", "Affiliate"]
    cmap = {c: ui.SERIES[i] for i, c in enumerate(order)}
    c1, c2 = st.columns(2)
    wk = d.assign(week=d.activity_date.astype("datetime64[ns]").dt.to_period("W").dt.start_time) \
        .groupby(["week", "channel"], as_index=False)["cost"].sum()
    fig = px.bar(wk, x="week", y="cost", color="channel", color_discrete_map=cmap, category_orders={"channel": order})
    fig.update_layout(yaxis_tickprefix="£")
    c1.plotly_chart(ui.style(fig, "Weekly spend by channel"), width="stretch")
    fig = px.bar(ch.sort_values("roas"), x="roas", y="channel", orientation="h", color="channel",
                 color_discrete_map=cmap, text_auto=".2f")
    fig.update_layout(showlegend=False, hovermode="closest")
    c2.plotly_chart(ui.style(fig, "ROAS by channel"), width="stretch")
    st.markdown("**Channel performance**")
    st.dataframe(ch, hide_index=True, width="stretch", column_config={
        "cost": st.column_config.NumberColumn(format="£%.0f"), "attributed_revenue": st.column_config.NumberColumn(format="£%.0f"),
        "ctr": st.column_config.NumberColumn(format="%.3f"), "conversion_rate": st.column_config.NumberColumn(format="%.3f"),
        "cac": st.column_config.NumberColumn(format="£%.2f"), "roas": st.column_config.NumberColumn(format="%.2fx")})
    ui.download_buttons(ch, "channel_performance_synthetic", "dl_mkt")
    daily = d.groupby(["activity_date", "channel"], as_index=False)[["clicks", "conversions"]].sum()
    daily["cvr_7d"] = daily.groupby("channel").conversions.transform(lambda s: s.rolling(7).sum()) / \
        daily.groupby("channel").clicks.transform(lambda s: s.rolling(7).sum())
    fig = px.line(daily, x="activity_date", y="cvr_7d", color="channel", color_discrete_map=cmap,
                  category_orders={"channel": order})
    fig.update_layout(yaxis_tickformat=".1%")
    ui.chart(ui.style(fig, "7-day rolling conversion rate"), "mkt_cvr")
    st.caption("The synthetic generator injects two documented anomalies (Paid Social spend spike 2011-09-12, Paid "
               "Search conversion drop 2011-11-01) so the Alerts module can demonstrate detection.")
