"""Exploratory + statistical analysis with business-language interpretations.

Every function returns numbers computed from data plus a plain-English
``interpretation`` that states what the test can and cannot conclude.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from ..db import query_df


# ------------------------------------------------------------------ EDA / profiling
def profile(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in df.columns:
        s = df[col]
        row = {"column": col, "dtype": str(s.dtype), "non_null": int(s.notna().sum()),
               "missing": int(s.isna().sum()), "missing_pct": float(s.isna().mean()), "distinct": int(s.nunique())}
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            x = s.dropna().astype(float)
            if len(x):
                q1, q3 = x.quantile([.25, .75])
                iqr = q3 - q1
                row.update(mean=x.mean(), std=x.std(), min=x.min(), p25=q1, median=x.median(), p75=q3, max=x.max(),
                           skew=float(stats.skew(x)) if len(x) > 2 else None,
                           outliers_iqr=int(((x < q1 - 1.5 * iqr) | (x > q3 + 1.5 * iqr)).sum()))
        else:
            top = s.astype(str).value_counts().head(1)
            if len(top):
                row.update(top_value=top.index[0], top_freq=int(top.iloc[0]))
        rows.append(row)
    return pd.DataFrame(rows)


def iqr_outliers(s: pd.Series, k: float = 1.5) -> pd.Series:
    q1, q3 = s.quantile([.25, .75])
    iqr = q3 - q1
    return (s < q1 - k * iqr) | (s > q3 + k * iqr)


def correlation(df: pd.DataFrame, method: str = "spearman") -> pd.DataFrame:
    num = df.select_dtypes("number")
    return num.corr(method=method)


def describe_correlation(r: float, a: str, b: str) -> str:
    strength = "negligible" if abs(r) < .1 else "weak" if abs(r) < .3 else "moderate" if abs(r) < .5 else "strong"
    direction = "positive" if r > 0 else "negative"
    return (f"{a} and {b} show a {strength} {direction} association (rho = {r:.2f}). "
            "Correlation describes co-movement only; it does not show that one causes the other.")


# ------------------------------------------------------------------ trend
def trend_test(series: pd.Series, label: str = "metric") -> dict:
    """OLS slope on a time index + Mann-Kendall-style Kendall tau test (robust to outliers)."""
    y = series.dropna().astype(float).to_numpy()
    if len(y) < 4:
        return {"interpretation": "Not enough periods for a trend test (need >= 4)."}
    x = np.arange(len(y))
    lr = stats.linregress(x, y)
    tau, p_tau = stats.kendalltau(x, y)
    rel = lr.slope / np.mean(y) if np.mean(y) else np.nan
    sig = p_tau < 0.05
    text = (f"{label}: average change of {lr.slope:,.1f} per period ({rel:+.1%} of the mean). "
            f"Kendall tau = {tau:.2f}, p = {p_tau:.3f} -> "
            + ("a statistically significant monotonic trend." if sig else
               "no statistically significant monotonic trend; changes may reflect seasonality or noise."))
    return {"slope": lr.slope, "slope_pct_of_mean": rel, "r2": lr.rvalue ** 2, "kendall_tau": tau, "p_value": p_tau,
            "significant": sig, "interpretation": text}


def seasonal_decomposition(daily: pd.Series, period: int = 7) -> pd.DataFrame:
    from statsmodels.tsa.seasonal import STL

    s = daily.asfreq("D").fillna(0.0)
    res = STL(s, period=period, robust=True).fit()
    return pd.DataFrame({"observed": s, "trend": res.trend, "seasonal": res.seasonal, "resid": res.resid})


# ------------------------------------------------------------------ hypothesis tests
def compare_groups(a: pd.Series, b: pd.Series, name_a: str, name_b: str, metric: str) -> dict:
    a, b = a.dropna().astype(float), b.dropna().astype(float)
    if len(a) < 5 or len(b) < 5:
        return {"interpretation": "Too few observations in one group for a reliable test."}
    t = stats.ttest_ind(a, b, equal_var=False)
    mw = stats.mannwhitneyu(a, b, alternative="two-sided")
    diff = a.mean() - b.mean()
    pooled = np.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2)
    d = diff / pooled if pooled else 0.0
    sig = mw.pvalue < 0.05
    size = "negligible" if abs(d) < .2 else "small" if abs(d) < .5 else "medium" if abs(d) < .8 else "large"
    text = (f"Mean {metric}: {name_a} = {a.mean():,.2f} (n={len(a):,}) vs {name_b} = {b.mean():,.2f} (n={len(b):,}); "
            f"medians {a.median():,.2f} vs {b.median():,.2f}. Welch t-test p = {t.pvalue:.4f}; Mann-Whitney p = {mw.pvalue:.4f}; "
            f"effect size (Cohen's d) = {d:.2f} ({size}). "
            + ("The difference is statistically significant" if sig else "The difference is not statistically significant")
            + ". This is an observational comparison - other factors (customer mix, season, order size) may explain it.")
    return {"mean_a": a.mean(), "mean_b": b.mean(), "median_a": a.median(), "median_b": b.median(), "t_p": t.pvalue,
            "mw_p": mw.pvalue, "cohens_d": d, "significant": sig, "interpretation": text}


def chi_square_rates(table: pd.DataFrame, label: str) -> dict:
    """table: rows = groups, columns = [events, non_events]."""
    chi2, p, dof, _ = stats.chi2_contingency(table.to_numpy())
    rates = table.iloc[:, 0] / table.sum(axis=1)
    text = (f"{label} differs across groups (chi-square = {chi2:,.1f}, dof = {dof}, p = {p:.4f}). "
            if p < 0.05 else f"No statistically significant difference in {label} across groups (p = {p:.3f}). ")
    text += "Highest: " + ", ".join(f"{i} {v:.1%}" for i, v in rates.sort_values(ascending=False).head(3).items()) + "."
    return {"chi2": chi2, "p_value": p, "dof": dof, "rates": rates, "interpretation": text}


# ------------------------------------------------------------------ anomaly detection
def zscore_anomalies(series: pd.Series, window: int = 28, threshold: float = 3.0) -> pd.DataFrame:
    """Rolling robust z-score vs trailing window (median / MAD), excluding the current point."""
    s = series.astype(float)
    med = s.shift(1).rolling(window, min_periods=max(7, window // 2)).median()
    mad = (s.shift(1) - med).abs().rolling(window, min_periods=max(7, window // 2)).median()
    z = 0.6745 * (s - med) / mad.replace(0, np.nan)
    return pd.DataFrame({"value": s, "expected": med, "robust_z": z, "is_anomaly": z.abs() >= threshold})


def isolation_forest_anomalies(df: pd.DataFrame, contamination: float = 0.02, seed: int = 7) -> pd.Series:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    X = StandardScaler().fit_transform(df.fillna(df.median()))
    model = IsolationForest(contamination=contamination, random_state=seed).fit(X)
    return pd.Series(model.predict(X) == -1, index=df.index, name="is_anomaly")


# ------------------------------------------------------------------ cohorts / variance
def cohort_matrix(max_months: int = 12) -> pd.DataFrame:
    df = query_df("select cohort, months_since_first, retention_rate from gold.mart_cohort_retention "
                  "where months_since_first <= ?", [max_months])
    return df.pivot(index="cohort", columns="months_since_first", values="retention_rate")


def variance_table(actual: pd.Series, plan: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"actual": actual, "plan": plan})
    df["variance"] = df["actual"] - df["plan"]
    df["variance_pct"] = df["variance"] / df["plan"].replace(0, np.nan)
    return df
