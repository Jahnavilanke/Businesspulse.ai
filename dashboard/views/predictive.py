from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from dashboard import components as ui


def render() -> None:
    ui.page_header("Predictive Analytics", "Forecasts, churn and customer-value models with honest, out-of-time "
                   "evaluation against baselines.", "actual")
    if not ui.require_warehouse():
        return
    if not ui.has("ml", "revenue_forecast"):
        ui.empty_state("Models not trained yet", "Run <code>python -m businesspulse.pipeline.runner</code> "
                       "(without --skip-ml) to train and evaluate the models.", "🔮")
        return
    runs = ui.q("""select model_name, model_variant, metric_name, metric_value, validation, trained_at, mlflow_run_id
                   from ops.model_runs r where run_id = (select run_id from ops.model_runs m2
                   where m2.model_name = r.model_name order by trained_at desc limit 1)""")
    t1, t2, t3, t4 = st.tabs(["Revenue forecast", "Demand forecast", "Churn prediction", "Customer value (CLV)"])
    with t1:
        hist = ui.q("""select date_trunc('week', invoice_date) as week, sum(line_revenue) net_revenue from gold.fact_sales
                       where line_type='product' group by 1 order by 1""")
        fc = ui.q("select * from ml.revenue_forecast")
        bt = ui.q("select * from ml.revenue_backtest")
        fig = go.Figure()
        fig.add_scatter(x=hist.week, y=hist.net_revenue, name="Actual", mode="lines", line_color=ui.SERIES[0])
        fig.add_scatter(x=fc.week, y=fc.upper, mode="lines", line=dict(width=0, color="rgba(0,0,0,0)"),
                        showlegend=False, hoverinfo="skip")
        # Revenue cannot be negative, so the band is floored at zero.
        fig.add_scatter(x=fc.week, y=fc.lower.clip(lower=0), mode="lines", line=dict(width=0, color="rgba(0,0,0,0)"),
                        fill="tonexty",
                        fillcolor="rgba(235,104,52,.15)", name="~95% band (backtest residuals)")
        fig.add_scatter(x=fc.week, y=fc.forecast, name=f"Forecast ({fc.model.iloc[0]})", mode="lines+markers",
                        line=dict(color=ui.SERIES[1], dash="dash"))
        fig.update_layout(yaxis_tickprefix="£", yaxis_tickformat="~s")
        ui.chart(ui.style(fig, "Weekly net revenue: history and 12-week forecast", 400), "rf")
        st.caption("Partial first/last weeks are excluded from training. Forecast horizon extends beyond the dataset "
                   "end (2011-12-09) - these are model projections, not observed outcomes.")
        m = runs[runs.model_name == "revenue_forecast"].pivot_table(index="model_variant", columns="metric_name",
                                                                   values="metric_value").sort_values("wape")
        st.markdown("**Backtest - model comparison** (lower is better)")
        st.dataframe(m, width="stretch", column_config={"wape": st.column_config.NumberColumn(format="%.3f"),
                                                        "mape": st.column_config.NumberColumn(format="%.3f"),
                                                        "mae": st.column_config.NumberColumn(format="£%.0f")})
        bl = bt.melt(id_vars="week", var_name="series", value_name="value")
        fig = px.line(bl, x="week", y="value", color="series", color_discrete_sequence=ui.SERIES)
        ui.chart(ui.style(fig, "Holdout weeks: actual vs each model", 320), "rf_bt")
        st.caption(runs[runs.model_name == "revenue_forecast"].validation.iloc[0])
    with t2:
        m = runs[runs.model_name == "demand_forecast"].pivot_table(index="model_variant", columns="metric_name",
                                                                  values="metric_value").sort_values("wape")
        st.markdown("**Product demand forecast - backtest across top products** (WAPE, lower is better)")
        st.dataframe(m, width="stretch")
        best = m.index[0]
        st.info(f"Best model on the holdout: **{best}**. "
                + ("A simple baseline outperformed the gradient-boosted model, so the baseline is used for the "
                   "forward forecast - reported as measured." if best != "xgb" else "XGBoost beat the baselines."))
        st.caption(runs[runs.model_name == "demand_forecast"].validation.iloc[0] + ". Product-level forecasts "
                   "are shown on the Inventory page.")
    with t3:
        m = runs[runs.model_name == "churn"].pivot_table(index="model_variant", columns="metric_name",
                                                        values="metric_value")
        st.markdown("**Churn (no purchase in next 90 days) - out-of-time evaluation**")
        st.dataframe(m, width="stretch")
        st.caption(runs[runs.model_name == "churn"].validation.iloc[0] + ". ROC/PR AUC higher is better; Brier lower.")
        fi = ui.q("select * from ml.churn_feature_importance order by mean_abs_shap")
        fig = px.bar(fi, x="mean_abs_shap", y="feature", orientation="h", color_discrete_sequence=[ui.SERIES[0]])
        fig.update_layout(hovermode="closest")
        ui.chart(ui.style(fig, "Feature importance - mean |SHAP| (XGBoost)", 420), "shap")
        st.caption("SHAP values explain the model's predictions; they describe associations learned from history, "
                   "not causal effects. Per-customer top drivers are on the Customer Analytics page.")
        sc = ui.q("select risk_band, count(*) n, avg(churn_probability) p from ml.churn_scores group by 1")
        st.dataframe(sc, hide_index=True)
    with t4:
        m = runs[runs.model_name == "clv_180d"].pivot_table(index="model_variant", columns="metric_name",
                                                           values="metric_value")
        st.markdown("**Customer value - predicted net revenue in the next 180 days**")
        st.dataframe(m, width="stretch")
        st.caption(runs[runs.model_name == "clv_180d"].validation.iloc[0] + ". MAE lower is better; Spearman and "
                   "top-decile revenue capture higher is better.")
        v = ui.q("""select v.*, c.rfm_segment from ml.clv_scores v join gold.dim_customer c using (customer_key)""")
        seg = v.groupby("rfm_segment", as_index=False).predicted_value_180d.sum().sort_values("predicted_value_180d")
        fig = px.bar(seg, x="predicted_value_180d", y="rfm_segment", orientation="h",
                     color_discrete_sequence=[ui.SERIES[0]])
        fig.update_layout(xaxis_tickprefix="£", hovermode="closest")
        ui.chart(ui.style(fig, "Predicted 180-day value by segment"), "clv_seg")
    with st.expander("Experiment tracking"):
        st.write("All runs are logged to `ops.model_runs` and to MLflow (local store `data/mlruns`, or the server set "
                 "in `MLFLOW_TRACKING_URI`). Launch the UI with `mlflow ui --backend-store-uri data/mlruns`.")
        st.dataframe(runs, hide_index=True, width="stretch")
