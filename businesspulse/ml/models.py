"""Predictive models with baselines and time-based validation.

* Revenue forecast  - weekly net revenue; seasonal-naive / moving-average / Holt-Winters / XGBoost
* Demand forecast   - weekly units for top products; seasonal-naive / moving-average / global XGBoost
* Churn             - P(no purchase in next 90 days); logistic regression vs XGBoost, out-of-time test + SHAP
* Customer value    - next-180-day revenue (CLV horizon); persistence baseline vs XGBoost (Tweedie)

All metrics reported come from held-out, later-in-time data. Nothing is hardcoded.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, mean_absolute_error, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier, XGBRegressor

from .features import FEATURES, customer_features, future_activity

warnings.filterwarnings("ignore", category=FutureWarning)
SEED = 42


def wape(y, yhat) -> float:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.abs(y - yhat).sum() / np.abs(y).sum()) if np.abs(y).sum() else float("nan")


def mape(y, yhat) -> float:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    m = y != 0
    return float(np.mean(np.abs((y[m] - yhat[m]) / y[m])))


# ------------------------------------------------------------------ weekly series helpers
def weekly_revenue(con) -> pd.Series:
    df = con.execute("""
        select date_trunc('week', invoice_date)::date as week, sum(line_revenue) as net_revenue,
               count(distinct invoice_date) as days
        from gold.fact_sales where line_type = 'product' group by 1 order by 1
    """).df()
    df["week"] = pd.to_datetime(df["week"])
    last = df["week"].max()
    max_date = pd.Timestamp(con.execute("select max(invoice_date) from gold.fact_sales").fetchone()[0])
    # drop incomplete first/last weeks so they don't look like collapses
    df = df[(df["week"] > df["week"].min()) & ~((df["week"] == last) & (max_date < last + pd.Timedelta(days=6)))]
    return df.set_index("week")["net_revenue"].asfreq("W-MON").fillna(0.0)


def _lag_frame(s: pd.Series, lags=(1, 2, 3, 4, 52)) -> pd.DataFrame:
    X = pd.DataFrame(index=s.index)
    for l in lags:
        X[f"lag_{l}"] = s.shift(l)
    X["ma_4"] = s.shift(1).rolling(4).mean()
    X["week_of_year"] = s.index.isocalendar().week.astype(int).to_numpy()
    X["is_q4"] = s.index.month.isin([10, 11, 12]).astype(int)
    return X


def revenue_forecast(con, horizon: int = 12, holdout: int = 12) -> dict:
    s = weekly_revenue(con)
    train, test = s.iloc[:-holdout], s.iloc[-holdout:]
    preds = {}
    preds["seasonal_naive_52w"] = s.shift(52).iloc[-holdout:].to_numpy()
    preds["moving_average_4w"] = np.repeat(train.iloc[-4:].mean(), holdout)
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            hw = ExponentialSmoothing(train, trend="add", seasonal="add", seasonal_periods=52,
                                      initialization_method="estimated").fit()
        preds["holt_winters"] = hw.forecast(holdout).to_numpy()
    except Exception:
        pass
    # XGBoost: recursive one-step forecasting with lag features
    X = _lag_frame(s)
    Xtr = X.loc[train.index].dropna()
    model = XGBRegressor(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.9, random_state=SEED)
    model.fit(Xtr, train.loc[Xtr.index])
    hist = train.copy()
    out = []
    for ts in test.index:
        ext = pd.concat([hist, pd.Series([np.nan], index=[ts])])
        row = _lag_frame(ext).loc[[ts]]
        yhat = float(model.predict(row)[0])
        out.append(yhat)
        hist = pd.concat([hist, pd.Series([yhat], index=[ts])])
    preds["xgboost_lags"] = np.array(out)
    metrics = {name: {"wape": wape(test, p), "mape": mape(test, p), "mae": float(mean_absolute_error(test, p))}
               for name, p in preds.items()}
    best = min(metrics, key=lambda k: metrics[k]["wape"])
    # Refit best on all data and forecast forward
    future_idx = pd.date_range(s.index[-1] + pd.Timedelta(weeks=1), periods=horizon, freq="W-MON")
    if best == "seasonal_naive_52w":
        fc = pd.Series([s.get(t - pd.Timedelta(weeks=52), np.nan) for t in future_idx], index=future_idx)
    elif best == "moving_average_4w":
        fc = pd.Series(s.iloc[-4:].mean(), index=future_idx)
    elif best == "holt_winters":
        hw = ExponentialSmoothing(s, trend="add", seasonal="add", seasonal_periods=52,
                                  initialization_method="estimated").fit()
        fc = hw.forecast(horizon)
        fc.index = future_idx
    else:
        model.fit(X.dropna(), s.loc[X.dropna().index])
        hist, vals = s.copy(), []
        for ts in future_idx:
            ext = pd.concat([hist, pd.Series([np.nan], index=[ts])])
            yhat = float(model.predict(_lag_frame(ext).loc[[ts]])[0])
            vals.append(yhat)
            hist = pd.concat([hist, pd.Series([yhat], index=[ts])])
        fc = pd.Series(vals, index=future_idx)
    resid = test.to_numpy() - preds[best]
    band = 1.96 * np.std(resid)
    forecast = pd.DataFrame({"week": future_idx, "forecast": fc.to_numpy(), "lower": fc.to_numpy() - band,
                             "upper": fc.to_numpy() + band, "model": best})
    backtest = pd.DataFrame({"week": test.index, "actual": test.to_numpy(), **{k: v for k, v in preds.items()}})
    return {"metrics": metrics, "best": best, "forecast": forecast, "backtest": backtest, "history": s,
            "validation": f"time-based holdout: last {holdout} complete weeks ({test.index[0].date()} to {test.index[-1].date()})"}


def demand_forecast(con, top_n: int = 50, holdout: int = 8, horizon: int = 4) -> dict:
    df = con.execute(f"""
        with top as (
            select stock_code from gold.fact_sales where line_type = 'product' and not is_cancellation
            group by 1 order by sum(quantity) desc limit {top_n}
        )
        select f.stock_code, date_trunc('week', f.invoice_date)::date as week, sum(f.quantity) as units
        from gold.fact_sales f join top using (stock_code)
        where f.line_type = 'product' and not f.is_cancellation
        group by 1, 2
    """).df()
    df["week"] = pd.to_datetime(df["week"])
    s_idx = weekly_revenue(con).index
    panel = (df.pivot_table(index="week", columns="stock_code", values="units", aggfunc="sum")
               .reindex(s_idx).fillna(0.0))
    rows = []
    for code in panel.columns:
        y = panel[code]
        X = _lag_frame(y, lags=(1, 2, 3, 4, 52))
        X["stock_code_mean"] = y.shift(1).expanding().mean()
        X["y"] = y
        X["stock_code"] = code
        rows.append(X)
    long = pd.concat(rows)
    cut = s_idx[-holdout]
    feats = [c for c in long.columns if c not in {"y", "stock_code"}]
    tr = long[(long.index < cut)].dropna(subset=feats)
    te = long[long.index >= cut].dropna(subset=feats)
    model = XGBRegressor(n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.9, objective="reg:tweedie",
                         random_state=SEED)
    model.fit(tr[feats], tr["y"])
    te = te.assign(xgb=model.predict(te[feats]), seasonal_naive=te["lag_52"], ma_4=te["ma_4"])
    metrics = {k: {"wape": wape(te["y"], te[k]), "mae": float(mean_absolute_error(te["y"], te[k]))}
               for k in ["xgb", "seasonal_naive", "ma_4"]}
    best = min(metrics, key=lambda k: metrics[k]["wape"])
    fut = []
    if best != "xgb":
        # A simple baseline won the backtest -> use it for the forward forecast (flat projection)
        for code in panel.columns:
            y = panel[code]
            level = y.iloc[-4:].mean() if best == "ma_4" else None
            for h in range(1, horizon + 1):
                ts = s_idx[-1] + pd.Timedelta(weeks=h)
                val = level if best == "ma_4" else float(y.get(ts - pd.Timedelta(weeks=52), 0.0))
                fut.append((code, ts, float(val)))
        forecast = pd.DataFrame(fut, columns=["stock_code", "week", "forecast_units"]).assign(model=best)
        return {"metrics": metrics, "best": best, "forecast": forecast, "holdout_rows": len(te), "products": top_n,
                "validation": f"time-based holdout: last {holdout} weeks across top {top_n} products"}
    # Recursive multi-step forecast with the global XGBoost model refit on all data
    model.fit(long.dropna(subset=feats)[feats], long.dropna(subset=feats)["y"])
    for code in panel.columns:
        y = panel[code]
        hist = y.copy()
        for h in range(1, horizon + 1):
            ts = s_idx[-1] + pd.Timedelta(weeks=h)
            ext = pd.concat([hist, pd.Series([np.nan], index=[ts])])
            X = _lag_frame(ext)
            X["stock_code_mean"] = ext.shift(1).expanding().mean()
            yhat = max(0.0, float(model.predict(X.loc[[ts], feats])[0]))
            fut.append((code, ts, yhat))
            hist = pd.concat([hist, pd.Series([yhat], index=[ts])])
    forecast = pd.DataFrame(fut, columns=["stock_code", "week", "forecast_units"]).assign(model=best)
    return {"metrics": metrics, "best": best, "forecast": forecast, "holdout_rows": len(te), "products": top_n,
            "validation": f"time-based holdout: last {holdout} weeks across top {top_n} products"}


def churn_model(con, horizon: int = 90) -> dict:
    d1 = pd.Timestamp(con.execute("select max(invoice_date) from gold.fact_sales").fetchone()[0])
    test_cut = d1 - pd.Timedelta(days=horizon)          # label window ends at data end
    train_cut = test_cut - pd.Timedelta(days=horizon)   # strictly earlier, non-overlapping label window

    def labelled(cut):
        f = customer_features(con, cut)
        fut = future_activity(con, cut, horizon)
        f = f.merge(fut, on="customer_key", how="left").fillna({"future_orders": 0, "future_revenue": 0})
        f["churned"] = (f["future_orders"] == 0).astype(int)
        return f

    tr, te = labelled(train_cut), labelled(test_cut)
    Xtr, Xte = tr[FEATURES].fillna(-1), te[FEATURES].fillna(-1)
    base = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced"))
    base.fit(Xtr, tr["churned"])
    xgb = XGBClassifier(n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9,
                        random_state=SEED, eval_metric="logloss")
    xgb.fit(Xtr, tr["churned"])
    res = {}
    for name, m in [("logistic_regression", base), ("xgboost", xgb)]:
        p = m.predict_proba(Xte)[:, 1]
        res[name] = {"roc_auc": roc_auc_score(te["churned"], p), "pr_auc": average_precision_score(te["churned"], p),
                     "brier": brier_score_loss(te["churned"], p)}
    res["base_rate"] = {"test_churn_rate": float(te["churned"].mean()), "train_churn_rate": float(tr["churned"].mean())}
    best = max(["logistic_regression", "xgboost"], key=lambda k: res[k]["roc_auc"])
    # SHAP explanations on the XGBoost model
    importance = None
    try:
        import shap
        explainer = shap.TreeExplainer(xgb)
        sv = explainer.shap_values(Xte)
        importance = (pd.DataFrame({"feature": FEATURES, "mean_abs_shap": np.abs(sv).mean(axis=0)})
                      .sort_values("mean_abs_shap", ascending=False))
    except Exception:
        importance = pd.DataFrame({"feature": FEATURES, "mean_abs_shap": xgb.feature_importances_}).sort_values(
            "mean_abs_shap", ascending=False)
    # Score all customers as of data end (labels unknown -> prediction)
    final = xgb if best == "xgboost" else base
    final.fit(pd.concat([Xtr, Xte]), pd.concat([tr["churned"], te["churned"]]))
    cur = customer_features(con, d1 + pd.Timedelta(days=1))
    cur["churn_probability"] = final.predict_proba(cur[FEATURES].fillna(-1))[:, 1]
    cur["risk_band"] = pd.cut(cur["churn_probability"], [0, .4, .7, 1.0], labels=["Low", "Medium", "High"],
                              include_lowest=True).astype(str)
    # Local explanations: SHAP values for XGBoost; exact coef x standardized-feature contributions for
    # the linear model (equivalent to linear SHAP with a mean background).
    Xcur = cur[FEATURES].fillna(-1)
    if best == "xgboost":
        try:
            import shap
            contrib = shap.TreeExplainer(final).shap_values(Xcur)
        except Exception:
            contrib = None
    else:
        scaler, lr = final.named_steps["standardscaler"], final.named_steps["logisticregression"]
        contrib = scaler.transform(Xcur) * lr.coef_[0]
    if contrib is not None:
        top_idx = np.argsort(-np.abs(contrib), axis=1)[:, :3]
        cur["top_drivers"] = [", ".join(f"{FEATURES[j]} ({'raises' if contrib[i, j] > 0 else 'lowers'} risk)"
                                        for j in row) for i, row in enumerate(top_idx)]
    else:
        cur["top_drivers"] = None
    return {"metrics": res, "best": best, "importance": importance,
            "scores": cur[["customer_key", "churn_probability", "risk_band", "top_drivers", "recency_days", "frequency",
                           "monetary"]],
            "validation": (f"out-of-time: train features < {train_cut.date()} (label {horizon}d after), "
                           f"test features < {test_cut.date()} (label {test_cut.date()} to {d1.date()})"),
            "n_train": len(tr), "n_test": len(te)}


def clv_model(con, horizon: int = 180) -> dict:
    d1 = pd.Timestamp(con.execute("select max(invoice_date) from gold.fact_sales").fetchone()[0])
    test_cut = d1 - pd.Timedelta(days=horizon)
    train_cut = test_cut - pd.Timedelta(days=horizon)

    def labelled(cut):
        f = customer_features(con, cut)
        fut = future_activity(con, cut, horizon)
        f = f.merge(fut, on="customer_key", how="left").fillna({"future_orders": 0, "future_revenue": 0})
        f["future_revenue"] = f["future_revenue"].clip(lower=0)
        past = con.execute("""select customer_key, coalesce(sum(order_revenue), 0) as past_revenue
                              from gold.fact_orders where customer_key <> -1
                                and invoice_date >= ?::date - ? and invoice_date < ?::date group by 1""",
                           [cut, horizon, cut]).df()
        return f.merge(past, on="customer_key", how="left").fillna({"past_revenue": 0})

    tr, te = labelled(train_cut), labelled(test_cut)
    model = XGBRegressor(n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.9, objective="reg:tweedie",
                         random_state=SEED)
    model.fit(tr[FEATURES].fillna(-1), tr["future_revenue"])
    preds = {"persistence_baseline": te["past_revenue"].clip(lower=0).to_numpy(),
             "xgboost_tweedie": model.predict(te[FEATURES].fillna(-1))}
    from scipy.stats import spearmanr
    metrics = {}
    y = te["future_revenue"].to_numpy()
    top = int(len(te) * 0.1)
    for name, p in preds.items():
        order = np.argsort(-p)[:top]
        metrics[name] = {"mae": float(mean_absolute_error(y, p)), "spearman": float(spearmanr(y, p).statistic),
                         "top_decile_revenue_capture": float(y[order].sum() / y.sum()) if y.sum() else None}
    full = pd.concat([tr, te])
    model.fit(full[FEATURES].fillna(-1), full["future_revenue"])
    cur = customer_features(con, d1 + pd.Timedelta(days=1))
    cur["predicted_value_180d"] = model.predict(cur[FEATURES].fillna(-1))
    cur["historical_value"] = cur["monetary"]
    return {"metrics": metrics, "scores": cur[["customer_key", "predicted_value_180d", "historical_value", "frequency",
                                                 "recency_days"]],
            "validation": f"out-of-time: train cutoff {train_cut.date()}, test cutoff {test_cut.date()}, horizon {horizon}d"}
