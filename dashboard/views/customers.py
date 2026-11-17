from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard import components as ui


def render() -> None:
    ui.page_header("Customer Analytics", "Acquisition, segments, lifetime value, churn risk and cohort retention.", "actual")
    if not ui.require_warehouse():
        return
    f = ui.filters()
    o = ui.q("""select date_trunc('month', invoice_date) as month, customer_type, count(distinct customer_key) customers,
                       sum(order_revenue) revenue
                from gold.fact_orders where not is_cancellation and invoice_date between ? and ? group by 1, 2 order by 1""",
             [f["start"], f["end"]])
    k = ui.kpi_values(f["start"], f["end"])
    ui.card_row([ui.kpi_card("Active customers", k["active_customers"], "count"),
                 ui.kpi_card("New customers", k["new_customers"], "count"),
                 ui.kpi_card("Repeat purchase rate", k["repeat_purchase_rate"], "ratio"),
                 ui.kpi_card("Avg monthly retention", k["retention_rate"], "ratio"),
                 ui.kpi_card("Guest share of orders",
                             o.loc[o.customer_type == "Guest", "revenue"].sum() / max(o.revenue.sum(), 1), "ratio",
                             help_text="Share of order revenue without a customer id")])
    t1, t2, t3, t4 = st.tabs(["New vs returning", "Segments", "Lifetime value & churn risk", "Cohort retention"])
    with t1:
        o2 = o[o.customer_type.isin(["New", "Returning"])]
        fig = px.bar(o2, x="month", y="customers", color="customer_type", barmode="stack",
                     color_discrete_map={"New": ui.SERIES[0], "Returning": ui.SERIES[1]})
        ui.chart(ui.style(fig, "Identified active customers per month"), "cust_nr")
        fig = px.bar(o, x="month", y="revenue", color="customer_type", barmode="stack",
                     color_discrete_map={"New": ui.SERIES[0], "Returning": ui.SERIES[1], "Guest": ui.SERIES[2]})
        fig.update_layout(yaxis_tickprefix="£", yaxis_tickformat="~s")
        ui.chart(ui.style(fig, "Order revenue by customer type"), "cust_rev")
    with t2:
        seg = ui.q("""select rfm_segment, loyalty_tier, count(*) customers, sum(lifetime_net_revenue) revenue,
                             avg(recency_days) avg_recency, avg(lifetime_orders) avg_orders
                      from gold.dim_customer where customer_key <> -1 and lifetime_orders > 0 group by all""")
        s1 = seg.groupby("rfm_segment", as_index=False)[["customers", "revenue"]].sum().sort_values("revenue")
        c1, c2 = st.columns(2)
        fig = go.Figure()
        fig.add_bar(y=s1.rfm_segment, x=s1.customers, orientation="h", name="Customers", marker_color=ui.SERIES[0])
        c1.plotly_chart(ui.style(fig, "Customers by RFM segment", 360).update_layout(hovermode="closest"), width="stretch")
        fig = go.Figure(go.Bar(y=s1.rfm_segment, x=s1.revenue, orientation="h", marker_color=ui.SERIES[0]))
        fig.update_layout(xaxis_tickprefix="£", xaxis_tickformat="~s", hovermode="closest")
        c2.plotly_chart(ui.style(fig, "Lifetime net revenue by segment", 360), width="stretch")
        st.caption("Segments and loyalty tiers are **derived** (RFM quintiles / revenue percentiles as of the last data "
                   "date) because the source has no CRM or loyalty programme data.")
        with st.expander("Data-driven clusters (K-Means on log-RFM)"):
            if st.button("Run clustering", key="km"):
                from businesspulse.analytics.segmentation import kmeans_segments
                with st.spinner("Clustering customers…"):
                    res = kmeans_segments()
                st.write(f"Selected k = **{res['k']}** by silhouette score: "
                         + ", ".join(f"k={k}: {v:.3f}" for k, v in res["silhouette"].items()))
                st.dataframe(res["profile"], hide_index=True, width="stretch")
    with t3:
        if not ui.has("ml", "churn_scores"):
            ui.empty_state("Models not trained", "Run the pipeline without --skip-ml to score churn and value.")
        else:
            d = ui.q("""select s.customer_key, c.home_country, c.rfm_segment, c.loyalty_tier, s.churn_probability,
                              s.risk_band, s.top_drivers, v.predicted_value_180d, c.lifetime_net_revenue, c.last_purchase_date
                       from ml.churn_scores s join gold.dim_customer c using (customer_key)
                       left join ml.clv_scores v using (customer_key) order by s.churn_probability desc""")
            c1, c2, c3 = st.columns(3)
            c1.metric("High churn-risk customers", f"{(d.risk_band == 'High').sum():,}")
            c2.metric("Lifetime revenue of high-risk", f"£{d.loc[d.risk_band == 'High', 'lifetime_net_revenue'].sum():,.0f}")
            c3.metric("Predicted 180-day value (all)", f"£{d.predicted_value_180d.sum():,.0f}")
            fig = px.histogram(d, x="churn_probability", nbins=40, color_discrete_sequence=[ui.SERIES[0]])
            fig.update_layout(hovermode="closest")
            ui.chart(ui.style(fig, "Distribution of predicted 90-day churn probability", 280), "churn_hist")
            band = st.multiselect("Risk band", ["High", "Medium", "Low"], default=["High"], key="riskband")
            view = d[d.risk_band.isin(band)]
            ui.searchable_table(view, "churn_tbl", 360, {
                "churn_probability": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                "predicted_value_180d": st.column_config.NumberColumn(format="£%.0f"),
                "lifetime_net_revenue": st.column_config.NumberColumn(format="£%.0f")})
            ui.download_buttons(view, "churn_risk_customers", "dl_churn")
            st.caption("CLV is reported as predicted net revenue over the next 180 days (explicit horizon). "
                       "See Predictive Analytics for out-of-time validation results.")
    with t4:
        from businesspulse.analytics.statistics import cohort_matrix
        mx = cohort_matrix(12)
        fig = px.imshow(mx, color_continuous_scale=ui.SEQUENTIAL, aspect="auto", text_auto=".0%",
                        labels=dict(x="Months since first purchase", y="Acquisition cohort", color="Active"))
        fig.update_layout(hovermode="closest")
        ui.chart(ui.style(fig, "Cohort retention (share of cohort purchasing in month N)", 620), "cohort")
        st.caption("Dec-2009 cohort includes customers acquired before the data starts (left-censoring), so it looks "
                   "unusually loyal.")
