from __future__ import annotations

import contextlib

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from businesspulse.analytics import statistics as stx
from businesspulse.analytics.kpis import METRICS, metric_catalog
from businesspulse.business_analysis.store import audit
from businesspulse.db import app_db, app_query
from businesspulse.security import can, run_readonly_sql
from dashboard import components as ui

EDA_SOURCES = {
    "Orders (gold.fact_orders)": "select order_revenue, units, line_count, distinct_products, customer_type, "
                                 "is_cancellation, invoice_date from gold.fact_orders",
    "Customers (gold.dim_customer)": "select lifetime_orders, lifetime_net_revenue, lifetime_returns, recency_days, "
                                     "home_country, rfm_segment, loyalty_tier from gold.dim_customer where customer_key <> -1",
    "Daily sales (gold.mart_daily_sales)": "select * from gold.mart_daily_sales",
    "Monthly KPIs (gold.mart_kpi_monthly)": "select * from gold.mart_kpi_monthly",
    "Silver transactions sample (50k lines)": "select * from silver.stg_transactions using sample 50000 rows",
}
SAMPLE_QUERIES = {
    "Top 10 customers by net revenue": "select customer_key, home_country, lifetime_orders, lifetime_net_revenue\n"
                                       "from gold.dim_customer where customer_key <> -1\norder by lifetime_net_revenue desc limit 10",
    "Monthly KPIs": "select year_month, net_revenue, orders, avg_order_value, active_customers, retention_rate\n"
                    "from gold.mart_kpi_monthly order by year_month",
    "Return rate by country (min £50k gross)": "select country_raw, sum(returns_value)/sum(gross_sales) as return_rate,\n"
                                               "       sum(gross_sales) gross\nfrom gold.fact_sales where line_type='product'\n"
                                               "group by 1 having sum(gross_sales) > 50000 order by 2 desc",
    "Quarantined rows by reason": "select dq_reasons, count(*) n from bronze.brz_quarantine_transactions group by 1 order by 2 desc",
    "List warehouse tables": "select table_schema, table_name from information_schema.tables order by 1, 2",
}


def _eda() -> None:
    src = st.selectbox("Dataset", list(EDA_SOURCES), key="eda_src")
    df = ui.q(EDA_SOURCES[src])
    st.caption(f"{len(df):,} rows × {df.shape[1]} columns")
    prof = stx.profile(df)
    st.markdown("**Profile & statistical summary**")
    st.dataframe(prof, hide_index=True, width="stretch")
    miss = prof[prof.missing > 0][["column", "missing", "missing_pct"]]
    if len(miss):
        st.markdown("**Missing values**")
        fig = px.bar(miss, x="column", y="missing_pct", color_discrete_sequence=[ui.SERIES[1]])
        fig.update_layout(yaxis_tickformat=".0%", hovermode="closest")
        ui.chart(ui.style(fig, None, 240), "eda_miss")
    num = [c for c in df.select_dtypes("number").columns if df[c].nunique() > 2]
    if num:
        c1, c2 = st.columns(2)
        with c1:
            col = st.selectbox("Distribution of", num, key="eda_col")
            log = st.toggle("Log scale (skewed data)", value=bool(df[col].skew() > 2), key="eda_log")
            x = df[col].dropna()
            out = stx.iqr_outliers(x)
            fig = px.histogram(np.log10(x[x > 0]) if log else x, nbins=60, color_discrete_sequence=[ui.SERIES[0]])
            fig.update_layout(showlegend=False, xaxis_title=f"log10({col})" if log else col, hovermode="closest")
            ui.chart(ui.style(fig, f"Distribution - {out.sum():,} IQR outliers ({out.mean():.1%})", 300), "eda_hist")
        with c2:
            if len(num) > 1:
                corr = stx.correlation(df[num])
                fig = px.imshow(corr, color_continuous_scale=ui.DIVERGING, zmin=-1, zmax=1, text_auto=".2f")
                fig.update_layout(hovermode="closest")
                ui.chart(ui.style(fig, "Spearman correlation", 340), "eda_corr")
                pairs = corr.where(np.triu(np.ones(corr.shape), 1).astype(bool)).stack()
                if len(pairs):
                    (a, b), r = max(pairs.items(), key=lambda kv: abs(kv[1]))
                    st.caption(stx.describe_correlation(r, a, b))
    if "date_day" in df.columns or "month" in df.columns:
        tcol = "date_day" if "date_day" in df.columns else "month"
        mcol = st.selectbox("Trend metric", [c for c in num if c != tcol], key="eda_trend")
        s = df.set_index(pd.to_datetime(df[tcol]))[mcol].sort_index()
        res = stx.trend_test(s.resample("ME").sum() if tcol == "date_day" else s, mcol)
        st.markdown(f'<div class="bp-insight">{res["interpretation"]}</div>', unsafe_allow_html=True)
        if tcol == "date_day":
            dec = stx.seasonal_decomposition(s.astype(float))
            fig = go.Figure()
            for i, c in enumerate(["trend", "seasonal"]):
                fig.add_scatter(x=dec.index, y=dec[c], name=c, mode="lines", line_color=ui.SERIES[i])
            ui.chart(ui.style(fig, "STL decomposition (weekly seasonality)", 300), "eda_stl")


def _sql_studio(user) -> None:
    if not can(user.role, "run_sql"):
        st.warning("Your role cannot run SQL.")
        return
    st.caption("Read-only access: single SELECT/WITH/DESCRIBE statements on the warehouse; file and network access "
               "disabled; results capped. Schemas: bronze, silver, gold, ml, ops, reference.")
    saved = app_query("select * from saved_queries order by name")
    c1, c2 = st.columns([2, 1])
    choice = c2.selectbox("Load query", ["—"] + list(SAMPLE_QUERIES) + [f"★ {n}" for n in saved.name],
                          key="sql_load")
    if choice != "—" and st.session_state.get("_sql_loaded") != choice:
        st.session_state["sql_text"] = (SAMPLE_QUERIES.get(choice) or
                                        saved.loc[saved.name == choice[2:], "sql_text"].iloc[0])
        st.session_state["_sql_loaded"] = choice
    sql = c1.text_area("SQL", key="sql_text", height=180, value=st.session_state.get("sql_text", SAMPLE_QUERIES["Monthly KPIs"]))
    b1, b2, b3 = st.columns([1, 2, 3])
    run = b1.button("▶ Run", type="primary", key="sql_run")
    name = b2.text_input("Save as", key="sql_name", placeholder="Query name", label_visibility="collapsed")
    if b3.button("💾 Save query", key="sql_save") and name.strip():
        with contextlib.closing(app_db()) as con:
            con.execute("insert or replace into saved_queries (name, sql_text, owner) values (?,?,?)",
                        [name.strip(), sql, user.username])
            con.commit()
        audit(user.username, user.role, "save_query", "saved_queries", name.strip())
        st.success(f"Saved '{name.strip()}'")
    if run:
        status, rows, ms, err = "success", None, None, None
        try:
            with st.spinner("Running query…"):
                df, ms = run_readonly_sql(sql)
            rows = len(df)
            st.success(f"{rows:,} rows in {ms:,.0f} ms")
            st.dataframe(df, width="stretch", height=420, hide_index=True)
            ui.download_buttons(df, "query_result", "sql_dl")
        except PermissionError as exc:
            status, err = "blocked", str(exc)
            st.error(f"Blocked: {exc}")
        except Exception as exc:
            status, err = "error", str(exc)
            st.error(f"SQL error: {exc}")
        with contextlib.closing(app_db()) as con:
            con.execute("insert into query_history (user_name, sql_text, status, row_count, duration_ms, error) "
                        "values (?,?,?,?,?,?)", [user.username, sql, status, rows, ms, err])
            con.commit()
    with st.expander("Query history (latest 50)"):
        st.dataframe(app_query("select ts, user_name, status, row_count, round(duration_ms,1) ms, sql_text, error "
                               "from query_history order by id desc limit 50"), hide_index=True, width="stretch")


def _kpi_analysis() -> None:
    f = ui.filters()
    st.markdown("**Governed metric definitions** (single source of truth, mirrored in dbt macros)")
    st.dataframe(metric_catalog(), hide_index=True, width="stretch")
    kind = st.radio("Compare with", ["prior", "yoy"], horizontal=True, key="kpi_kind",
                    format_func=lambda x: {"prior": "Previous period", "yoy": "Same period last year"}[x])
    cmp = ui.kpi_cmp(f["start"], f["end"], kind)
    st.markdown(f"**KPI values {f['start']} → {f['end']}**")
    st.dataframe(cmp, hide_index=True, width="stretch",
                 column_config={"change_pct": st.column_config.NumberColumn(format="percent")})
    ui.download_buttons(cmp, "kpi_analysis", "kpi_dl")
    m = ui.q("select * from gold.mart_kpi_monthly order by month")
    met = st.selectbox("Plot monthly KPI", [c for c in m.columns if c not in {"month", "year_month", "is_partial_month"}],
                       index=2, key="kpi_plot")
    fig = px.line(m, x="month", y=met, markers=True, color_discrete_sequence=[ui.SERIES[0]])
    ui.chart(ui.style(fig, met, 300), "kpi_line")


def _statistics() -> None:
    test = st.selectbox("Analysis", ["Hypothesis test: order value, new vs returning",
                                     "Hypothesis test: order value, UK vs international",
                                     "Chi-square: return incidence by region",
                                     "Variance analysis: monthly revenue vs prior year",
                                     "Anomaly detection: daily net revenue",
                                     "Cohort analysis", "Customer segmentation (K-Means)"], key="stat_sel")
    f = ui.filters()
    if test.startswith("Hypothesis test: order value, new"):
        o = ui.q("""select customer_type, order_revenue from gold.fact_orders
                    where not is_cancellation and customer_type in ('New','Returning') and invoice_date between ? and ?""",
                 [f["start"], f["end"]])
        r = stx.compare_groups(o[o.customer_type == "New"].order_revenue, o[o.customer_type == "Returning"].order_revenue,
                               "new customers", "returning customers", "order value (£)")
        st.markdown(f'<div class="bp-insight">{r["interpretation"]}</div>', unsafe_allow_html=True)
        fig = px.box(o, x="customer_type", y="order_revenue", log_y=True, color="customer_type",
                     color_discrete_sequence=ui.SERIES)
        ui.chart(ui.style(fig, "Order value distribution (log scale)", 320), "st_box")
    elif test.startswith("Hypothesis test: order value, UK"):
        o = ui.q("""select case when g.is_domestic then 'UK' else 'International' end market, o.order_revenue
                    from gold.fact_orders o join gold.dim_geography g using (geography_key)
                    where not o.is_cancellation and o.invoice_date between ? and ?""", [f["start"], f["end"]])
        r = stx.compare_groups(o[o.market == "International"].order_revenue, o[o.market == "UK"].order_revenue,
                               "international", "UK", "order value (£)")
        st.markdown(f'<div class="bp-insight">{r["interpretation"]}</div>', unsafe_allow_html=True)
    elif test.startswith("Chi-square"):
        t = ui.q("""select g.region, count(distinct invoice_no) filter (where is_cancellation) cancelled,
                           count(distinct invoice_no) filter (where not is_cancellation) not_cancelled
                    from gold.fact_sales f join gold.dim_geography g using (geography_key)
                    where line_type='product' and invoice_date between ? and ? group by 1""", [f["start"], f["end"]])
        t = t[t.region != "Unknown"].set_index("region")
        r = stx.chi_square_rates(t, "cancellation-invoice incidence")
        st.markdown(f'<div class="bp-insight">{r["interpretation"]}</div>', unsafe_allow_html=True)
        st.dataframe(t.assign(rate=r["rates"]), width="stretch")
    elif test.startswith("Variance"):
        m = ui.q("select month, net_revenue, is_partial_month from gold.mart_kpi_monthly order by month")
        s = m.set_index(pd.to_datetime(m.month)).net_revenue
        vt = stx.variance_table(s, s.shift(12)).dropna()
        st.dataframe(vt.reset_index(), hide_index=True, width="stretch",
                     column_config={"variance_pct": st.column_config.NumberColumn(format="percent")})
        worst = vt.variance_pct.idxmin()
        st.markdown(f'<div class="bp-insight">Largest negative YoY variance: {worst:%b %Y} '
                    f'({vt.variance_pct.min():+.1%}). Dec 2011 is a partial month and should be excluded from '
                    'conclusions.</div>', unsafe_allow_html=True)
    elif test.startswith("Anomaly"):
        d = ui.q("select date_day, net_revenue from gold.mart_daily_sales order by 1")
        s = d.set_index(pd.to_datetime(d.date_day)).net_revenue
        z = stx.zscore_anomalies(s, 28, 3.0)
        iso = stx.isolation_forest_anomalies(ui.q("select net_revenue, returns_value, orders, customers "
                                                  "from gold.mart_daily_sales order by date_day").set_index(s.index))
        fig = go.Figure()
        fig.add_scatter(x=z.index, y=z.value, mode="lines", name="Daily net revenue", line_color=ui.SERIES[0])
        a = z[z.is_anomaly]
        fig.add_scatter(x=a.index, y=a.value, mode="markers", name="Robust z ≥ 3", marker=dict(size=9, color=ui.SERIES[1]))
        b = z[iso.to_numpy()]
        fig.add_scatter(x=b.index, y=b.value, mode="markers", name="Isolation Forest",
                        marker=dict(size=12, symbol="circle-open", color=ui.SERIES[6], line_width=2))
        ui.chart(ui.style(fig, "Daily anomalies: robust z-score vs Isolation Forest (multivariate)", 380), "anom")
        st.caption(f"{int(z.is_anomaly.sum())} robust-z anomalies; {int(iso.sum())} Isolation Forest anomalies "
                   "(2% contamination). Anomalies flag unusual days to investigate - they are not explanations.")
    elif test.startswith("Cohort"):
        mx = stx.cohort_matrix(12)
        st.dataframe(mx.style.format("{:.0%}").background_gradient(cmap="Blues", axis=None), width="stretch")
        avg = mx.mean()
        st.markdown(f'<div class="bp-insight">On average {avg.get(1, float("nan")):.0%} of a cohort buys again in its '
                    f'second month and {avg.get(6, float("nan")):.0%} in month 6.</div>', unsafe_allow_html=True)
    else:
        from businesspulse.analytics.segmentation import kmeans_segments
        with st.spinner("Clustering…"):
            res = kmeans_segments()
        st.write(f"Best k = {res['k']} (silhouette {res['silhouette'][res['k']]:.3f}).")
        st.dataframe(res["profile"], hide_index=True, width="stretch")


def render() -> None:
    ui.page_header("Data Analyst Workspace", "Explore, query, measure and model the trusted warehouse data.", "actual")
    if not ui.require_warehouse():
        return
    u = ui.user()
    t1, t2, t3, t4 = st.tabs(["🔍 Exploratory analysis", "🧾 SQL Studio", "📐 KPI analysis", "📊 Statistical analysis"])
    with t1:
        _eda()
    with t2:
        _sql_studio(u)
    with t3:
        _kpi_analysis()
    with t4:
        _statistics()
