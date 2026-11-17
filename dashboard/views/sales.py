from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from businesspulse.analytics.kpis import shift_period
from dashboard import components as ui


def _where(f, alias="f"):
    w, p = [f"{alias}.invoice_date between ? and ?"], [f["start"], f["end"]]
    if f["countries"]:
        w.append(f"{alias}.country_raw in ({','.join('?' * len(f['countries']))})")
        p += f["countries"]
    if f["categories"]:
        w.append(f"p.category in ({','.join('?' * len(f['categories']))})")
        p += f["categories"]
    return " and ".join(w), p


def render() -> None:
    ui.page_header("Sales Analytics", "Trends, products, geography and revenue variance.", "actual")
    if not ui.require_warehouse():
        return
    f = ui.filters()
    k = ui.kpi_values(f["start"], f["end"], f["countries"], f["categories"])
    ps, pe = shift_period(f["start"], f["end"], "yoy")
    ky = ui.kpi_values(ps, pe, f["countries"], f["categories"])
    ch = lambda a, b: (a - b) / abs(b) if a is not None and b else None
    ui.card_row([ui.kpi_card("Net revenue", k["net_revenue"], "currency", ch(k["net_revenue"], ky["net_revenue"])),
                 ui.kpi_card("Orders", k["orders"], "count", ch(k["orders"], ky["orders"])),
                 ui.kpi_card("Avg order value", k["avg_order_value"], "currency", ch(k["avg_order_value"], ky["avg_order_value"])),
                 ui.kpi_card("Units sold", k["units_sold"], "count", ch(k["units_sold"], ky["units_sold"])),
                 ui.kpi_card("Return rate", k["return_rate"], "ratio", ch(k["return_rate"], ky["return_rate"]),
                             "lower_is_better")])
    st.caption(f"Deltas vs same period last year ({ps} → {pe}). Filters: "
               f"{', '.join(f['countries']) or 'all countries'}; {', '.join(f['categories']) or 'all categories'}.")
    w, p = _where(f)
    t1, t2, t3, t4 = st.tabs(["Trend", "Products", "Geography", "Channel & variance"])
    with t1:
        grain = st.segmented_control("Granularity", ["Day", "Week", "Month"], default="Week", key="sales_grain")
        trunc = {"Day": "day", "Week": "week", "Month": "month"}[grain or "Week"]
        d = ui.q(f"""select date_trunc('{trunc}', f.invoice_date) as period,
                        sum(f.line_revenue) filter (where f.line_type='product') net_revenue,
                        sum(f.returns_value) returns_value
                     from gold.fact_sales f join gold.dim_product p using (product_key) where {w} group by 1 order by 1""", p)
        fig = go.Figure()
        fig.add_scatter(x=d.period, y=d.net_revenue, name="Net revenue", mode="lines", line_color=ui.SERIES[0])
        fig.add_scatter(x=d.period, y=d.returns_value, name="Returns value", mode="lines", line_color=ui.SERIES[1])
        if trunc != "day" and len(d) > 4:
            fig.add_scatter(x=d.period, y=d.net_revenue.rolling(4).mean(), name="4-period moving avg", mode="lines",
                            line=dict(color=ui.MUTED, dash="dot"))
        fig.update_layout(yaxis_tickprefix="£", yaxis_tickformat="~s")
        ui.chart(ui.style(fig, f"Net revenue by {trunc}", 380), "sales_trend")
        dow = ui.q(f"""select dayname(f.invoice_date) dow, isodow(f.invoice_date) n,
                          sum(f.line_revenue) filter (where f.line_type='product') revenue
                       from gold.fact_sales f join gold.dim_product p using (product_key) where {w} group by 1,2 order by 2""", p)
        hr = ui.q(f"""select hour(f.invoice_ts) as hour_of_day, sum(f.line_revenue) filter (where f.line_type='product') revenue
                      from gold.fact_sales f join gold.dim_product p using (product_key) where {w} group by 1 order by 1""", p)
        c1, c2 = st.columns(2)
        c1.plotly_chart(ui.style(px.bar(dow, x="dow", y="revenue", color_discrete_sequence=[ui.SERIES[0]]),
                                 "Revenue by weekday (no Saturday trading in source)", 300), width="stretch")
        c2.plotly_chart(ui.style(px.bar(hr, x="hour_of_day", y="revenue", color_discrete_sequence=[ui.SERIES[0]]),
                                 "Revenue by hour of day", 300), width="stretch")
    with t2:
        prod = ui.q(f"""select p.stock_code, p.product_name, p.category,
                           sum(f.line_revenue) net_revenue, sum(f.quantity) filter (where not f.is_cancellation) units,
                           count(distinct f.invoice_no) filter (where not f.is_cancellation) orders,
                           sum(f.returns_value) returns_value
                        from gold.fact_sales f join gold.dim_product p using (product_key)
                        where {w} and f.line_type='product' group by all order by net_revenue desc""", p)
        cat = prod.groupby("category", as_index=False)["net_revenue"].sum().sort_values("net_revenue")
        c1, c2 = st.columns([2, 3])
        fig = px.bar(cat, x="net_revenue", y="category", orientation="h", color_discrete_sequence=[ui.SERIES[0]])
        fig.update_layout(xaxis_tickprefix="£", xaxis_tickformat="~s", hovermode="closest")
        c1.plotly_chart(ui.style(fig, "Net revenue by category", 460), width="stretch")
        with c2:
            st.markdown("**Product performance** (Pareto)")
            prod["cum_share"] = prod.net_revenue.cumsum() / prod.net_revenue.sum()
            n80 = int((prod.cum_share <= 0.8).sum()) + 1
            st.caption(f"{n80:,} of {len(prod):,} products ({n80 / max(len(prod), 1):.0%}) generate 80% of net revenue.")
            ui.searchable_table(prod, "sales_prod", 360, {"net_revenue": st.column_config.NumberColumn(format="£%.0f"),
                                                         "returns_value": st.column_config.NumberColumn(format="£%.0f"),
                                                         "cum_share": st.column_config.NumberColumn(format="%.2f")})
            ui.download_buttons(prod, "product_performance", "dl_prod")
    with t3:
        geo = ui.q(f"""select g.country_name, g.iso2, g.region, sum(f.line_revenue) filter (where f.line_type='product') net_revenue,
                          count(distinct f.customer_key) filter (where f.customer_key<>-1) customers
                       from gold.fact_sales f join gold.dim_geography g using (geography_key)
                       join gold.dim_product p using (product_key) where {w} group by all order by net_revenue desc""", p)
        c1, c2 = st.columns([3, 2])
        fig = px.choropleth(geo[geo.iso2.notna()], locations="country_name", locationmode="country names",
                            color="net_revenue", color_continuous_scale=ui.SEQUENTIAL, hover_name="country_name")
        fig.update_layout(geo=dict(showframe=False, projection_type="natural earth"), coloraxis_colorbar_title="£")
        c1.plotly_chart(ui.style(fig, "Net revenue by country", 420), width="stretch")
        ex_uk = st.toggle("Exclude United Kingdom (domestic market dominates)", value=True, key="geo_exuk")
        g2 = geo[geo.country_name != "United Kingdom"] if ex_uk else geo
        fig = px.bar(g2.head(15).iloc[::-1], x="net_revenue", y="country_name", orientation="h",
                     color_discrete_sequence=[ui.SERIES[0]])
        fig.update_layout(hovermode="closest")
        c2.plotly_chart(ui.style(fig, "Top countries", 420), width="stretch")
        uk_share = geo.loc[geo.country_name == "United Kingdom", "net_revenue"].sum() / geo.net_revenue.sum()
        st.caption(f"United Kingdom share of net revenue in range: {uk_share:.1%}.")
    with t4:
        st.info("**Channel contribution:** the source retailer sells through a single online channel, so all actual "
                "revenue is 'Online'. Store/channel comparisons would require data the source does not contain.")
        lt = ui.q(f"""select f.line_type, sum(f.line_revenue) revenue, count(*) lines from gold.fact_sales f
                      join gold.dim_product p using (product_key) where {w} group by 1 order by 2 desc""", p)
        st.markdown("**Revenue by line type** (product vs postage / fees / adjustments)")
        st.dataframe(lt, hide_index=True, width="stretch",
                     column_config={"revenue": st.column_config.NumberColumn(format="£%.0f")})
        st.markdown("**Monthly revenue variance vs same month last year**")
        m = ui.q("select month, net_revenue, is_partial_month from gold.mart_kpi_monthly order by month")
        m["month"] = pd.to_datetime(m["month"])
        m["last_year"] = m.set_index("month").net_revenue.shift(12).to_numpy()
        m["variance"] = m.net_revenue - m.last_year
        v = m.dropna(subset=["last_year"])
        fig = go.Figure(go.Bar(x=v.month, y=v.variance,
                               marker_color=[ui.DIVERGING[4] if x >= 0 else ui.DIVERGING[0] for x in v.variance],
                               hovertemplate="%{x|%b %Y}: £%{y:,.0f}<extra></extra>"))
        fig.update_layout(yaxis_tickprefix="£", hovermode="closest")
        ui.chart(ui.style(fig, "YoY net revenue variance (Dec 2011 is a partial month)", 320), "sales_var")
