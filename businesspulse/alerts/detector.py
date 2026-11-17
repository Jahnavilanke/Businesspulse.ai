"""Automated business monitoring: statistical rules over governed metrics.

Rules (all thresholds are explicit and configurable here):
  revenue_decline        weekly net revenue robust-z <= -3 vs trailing 8 weeks, or full month
                         >15% below the same month last year                         [actual]
  returns_spike          weekly return rate robust-z >= 3 and rate >= 5%            [actual]
  retention_drop         full-month retention >5pp below trailing 3-month mean       [actual]
  inventory_shortage     stocked-out / below-reorder positions for forecast-demand
                         products                                                     [synthetic]
  marketing_spend_anomaly daily channel spend robust-z >= 4 and >= 1.8x median (episodes) [synthetic]
  conversion_rate_change 7-day channel CVR >30% below trailing 28-day CVR (>=50 conv/wk) [synthetic]

Alert ids are deterministic (rule:metric:period), so re-running detection updates the
evidence of existing alerts without resetting their workflow status.
"""
from __future__ import annotations

import contextlib
import json

import numpy as np
import pandas as pd

from ..db import app_db, migrate_app_db, query_df, table_exists
from ..ingestion import registry
from ..logging_utils import get_logger, log

logger = get_logger("alerts")
OWNERS = {"revenue_decline": "Head of Sales", "returns_spike": "Operations Manager", "retention_drop": "CRM Lead",
          "inventory_shortage": "Supply Chain Planner", "marketing_spend_anomaly": "Marketing Analytics",
          "conversion_rate_change": "Marketing Analytics"}


def _robust_z(s: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    base = s.shift(1).rolling(window, min_periods=max(4, window // 2))
    med = base.median()
    mad = (s.shift(1) - med).abs().rolling(window, min_periods=max(4, window // 2)).median()
    return med, 0.6745 * (s - med) / mad.replace(0, np.nan)


def _severity(score: float) -> str:
    a = abs(score)
    return "critical" if a >= 6 else "high" if a >= 4.5 else "medium" if a >= 3 else "low"


def _alert(rule, metric, period, detection_date, observed, expected, severity, evidence, investigation, basis="actual"):
    var = (observed - expected) / abs(expected) if expected not in (None, 0) and expected == expected else None
    return {"alert_id": f"{rule}:{metric}:{period}", "rule": rule, "metric": metric, "period": period,
            "detection_date": str(detection_date), "severity": severity, "observed_value": float(observed),
            "expected_value": None if expected is None else float(expected), "variance_pct": var,
            "evidence": json.dumps(evidence, default=str), "recommended_investigation": investigation,
            "data_basis": basis, "owner": OWNERS[rule]}


def _weekly() -> pd.DataFrame:
    df = query_df("""
        select date_trunc('week', invoice_date)::date as week, sum(line_revenue) filter (where line_type='product') as net_revenue,
               sum(gross_sales) as gross, sum(returns_value) as returns, count(distinct invoice_date) as days
        from gold.fact_sales group by 1 order by 1
    """)
    df["week"] = pd.to_datetime(df["week"])
    return df[df["days"] >= 4].set_index("week")        # skip partial weeks (holidays / data edges)


def detect_revenue(alerts: list) -> None:
    w = _weekly()
    med, z = _robust_z(w["net_revenue"], 8)
    for ts in w.index[z <= -3]:
        alerts.append(_alert("revenue_decline", "weekly_net_revenue", f"W{ts:%G-%V}", ts + pd.Timedelta(days=6),
                             w.at[ts, "net_revenue"], med[ts], _severity(z[ts]),
                             {"robust_z": round(z[ts], 2), "trading_days": int(w.at[ts, "days"]),
                              "baseline": "median of previous 8 complete weeks"},
                             "Open Root-Cause > 'Why did revenue decline?' for this week vs the prior 4 weeks; "
                             "check country and category contribution and large cancellations."))
    m = query_df("select month, net_revenue, is_partial_month from gold.mart_kpi_monthly order by month")
    m["month"] = pd.to_datetime(m["month"])
    m = m.set_index("month")
    for ts, row in m.iterrows():
        ly = ts - pd.DateOffset(years=1)
        if row.is_partial_month or ly not in m.index:
            continue
        exp = m.at[ly, "net_revenue"]
        chg = (row.net_revenue - exp) / exp
        if chg <= -0.15:
            alerts.append(_alert("revenue_decline", "monthly_net_revenue_yoy", f"{ts:%Y-%m}", ts + pd.offsets.MonthEnd(0),
                                 row.net_revenue, exp, "high" if chg <= -0.25 else "medium",
                                 {"yoy_change": round(chg, 4), "comparison_month": f"{ly:%Y-%m}"},
                                 "Compare the month with the same month last year by country, category and "
                                 "new vs returning customers."))


def detect_returns(alerts: list) -> None:
    w = _weekly()
    rate = w["returns"] / w["gross"]
    med, z = _robust_z(rate, 8)
    for ts in rate.index[(z >= 3) & (rate >= 0.05)]:
        top = query_df("""select invoice_no, stock_code, quantity, returns_value from gold.fact_sales
                          where is_cancellation and line_type='product' and invoice_date between ? and ?
                          order by returns_value desc limit 3""", [ts.date(), (ts + pd.Timedelta(days=6)).date()])
        alerts.append(_alert("returns_spike", "weekly_return_rate", f"W{ts:%G-%V}", ts + pd.Timedelta(days=6),
                             rate[ts], med[ts], _severity(z[ts]),
                             {"robust_z": round(z[ts], 2), "returns_value": round(w.at[ts, "returns"], 2),
                              "largest_cancellations": top.to_dict(orient="records")},
                             "Review the largest cancellation lines; confirm whether they reverse erroneous orders "
                             "(e.g. keying errors) or reflect product quality issues."))


def detect_retention(alerts: list) -> None:
    m = query_df("select month, retention_rate, is_partial_month from gold.mart_kpi_monthly order by month")
    m["month"] = pd.to_datetime(m["month"])
    m = m[~m["is_partial_month"]].dropna(subset=["retention_rate"]).set_index("month")
    trail = m["retention_rate"].shift(1).rolling(3).mean()
    drop = m["retention_rate"] - trail
    for ts in m.index[drop <= -0.05]:
        alerts.append(_alert("retention_drop", "monthly_retention_rate", f"{ts:%Y-%m}", ts + pd.offsets.MonthEnd(0),
                             m.at[ts, "retention_rate"], trail[ts], "high" if drop[ts] <= -0.08 else "medium",
                             {"drop_pp": round(drop[ts] * 100, 1), "baseline": "mean of previous 3 full months"},
                             "Open Root-Cause > 'Why did customer retention decrease?'; compare RFM segments and "
                             "cohort curves; check if the prior month was a seasonal peak."))


def detect_inventory(alerts: list) -> None:
    if not (table_exists("gold", "fact_inventory") and table_exists("ml", "demand_forecast")):
        return
    df = query_df("""
        with fc as (select stock_code, sum(forecast_units) as units_4w from ml.demand_forecast group by 1)
        select i.stock_code, sum(i.stock_qty) as stock, max(i.supplier_lead_time_days) as lead_days,
               fc.units_4w, bool_or(i.is_stockout) as any_stockout
        from gold.fact_inventory i join fc using (stock_code) group by i.stock_code, fc.units_4w
    """)
    df["daily"] = df["units_4w"] / 28
    df["lead_time_demand"] = df["daily"] * df["lead_days"]
    short = df[df["stock"] < df["lead_time_demand"]].sort_values("lead_time_demand", ascending=False)
    if len(short):
        snap = pd.Timestamp(query_df("select max(snapshot_date) d from gold.fact_inventory").d[0]).date()
        alerts.append(_alert("inventory_shortage", "products_below_lead_time_demand", f"{snap}", snap,
                             len(short), 0, "high" if len(short) >= 10 else "medium",
                             {"products": short.head(10)[["stock_code", "stock", "lead_time_demand"]].round(1)
                              .to_dict(orient="records"), "forecast_products": len(df)},
                             "Review reorder quantities for listed products against supplier lead times.",
                             basis="synthetic"))


def _episodes(flags: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    eps, start, prev = [], None, None
    for ts in flags.index[flags]:
        if start is None:
            start = prev = ts
        elif (ts - prev).days <= 2:
            prev = ts
        else:
            eps.append((start, prev))
            start = prev = ts
    if start is not None:
        eps.append((start, prev))
    return eps


def detect_marketing(alerts: list) -> None:
    if not table_exists("gold", "fact_marketing"):
        return
    d = query_df("""select channel, activity_date, sum(cost) cost, sum(clicks) clicks, sum(conversions) conv
                    from gold.fact_marketing group by 1, 2 order by 1, 2""")
    d["activity_date"] = pd.to_datetime(d["activity_date"])
    for ch, g in d.groupby("channel"):
        g = g.set_index("activity_date")
        med, z = _robust_z(g["cost"], 28)
        # statistical AND practical significance: robust-z >= 4 and spend >= 1.8x the trailing median
        for s, e in _episodes((z >= 4) & (g["cost"] >= 1.8 * med)):
            obs = g.loc[s:e, "cost"].mean()
            alerts.append(_alert("marketing_spend_anomaly", f"daily_spend_{ch}", f"{s:%Y-%m-%d}", e, obs, med[s],
                                 _severity(z[s:e].max()),
                                 {"channel": ch, "days": (e - s).days + 1, "max_robust_z": round(z[s:e].max(), 2),
                                  "conversions_avg": round(g.loc[s:e, "conv"].mean(), 1)},
                                 "Check campaign budget pacing / bidding changes for the channel.", basis="synthetic"))
        cvr7 = g["conv"].rolling(7).sum() / g["clicks"].rolling(7).sum()
        base = (g["conv"].shift(7).rolling(28).sum() / g["clicks"].shift(7).rolling(28).sum())
        rel = (cvr7 - base) / base
        # avoid small-sample noise: baseline must average >= 50 conversions per week
        enough = g["conv"].shift(7).rolling(28).sum() >= 200
        for s, e in _episodes((rel <= -0.30) & enough):
            alerts.append(_alert("conversion_rate_change", f"cvr_{ch}", f"{s:%Y-%m-%d}", e, cvr7[s:e].min(), base[s],
                                 "high" if rel[s:e].min() <= -0.45 else "medium",
                                 {"channel": ch, "min_relative_change": round(rel[s:e].min(), 3)},
                                 "Inspect landing pages, tracking tags and audience changes for the channel.",
                                 basis="synthetic"))


def upsert(alerts: list[dict]) -> dict:
    migrate_app_db()
    new = updated = 0
    with contextlib.closing(app_db()) as con:
        for a in alerts:
            exists = con.execute("select 1 from alerts where alert_id = ?", [a["alert_id"]]).fetchone()
            if exists:
                con.execute("""update alerts set severity=?, observed_value=?, expected_value=?, variance_pct=?,
                               evidence=?, recommended_investigation=?, updated_at=current_timestamp where alert_id=?""",
                            [a["severity"], a["observed_value"], a["expected_value"], a["variance_pct"], a["evidence"],
                             a["recommended_investigation"], a["alert_id"]])
                updated += 1
            else:
                cols = ", ".join(a)
                con.execute(f"insert into alerts ({cols}) values ({', '.join('?' * len(a))})", list(a.values()))
                con.execute("insert into alert_events (alert_id, user_name, from_status, to_status, note) values (?,?,?,?,?)",
                            [a["alert_id"], "system", None, "Open", "Detected by automated monitoring"])
                new += 1
        con.commit()
    return {"new": new, "updated": updated, "total_detected": len(alerts)}


def run_detection() -> dict:
    alerts: list[dict] = []
    errors = []
    for fn in (detect_revenue, detect_returns, detect_retention, detect_inventory, detect_marketing):
        try:
            fn(alerts)
        except Exception as exc:
            errors.append(f"{fn.__name__}: {exc}")
            log(logger, 40, "alert rule failed", rule=fn.__name__, error=str(exc))
    result = upsert(alerts)
    if errors:
        raise RuntimeError(f"{len(errors)} alert rule(s) failed: {errors}")
    result["by_rule"] = pd.Series([a["rule"] for a in alerts]).value_counts().to_dict() if alerts else {}
    log(logger, 20, "alert detection complete", **{k: v for k, v in result.items() if k != "by_rule"})
    return result


VALID_TRANSITIONS = {"Open": {"Acknowledged", "Investigating", "Dismissed"},
                     "Acknowledged": {"Investigating", "Resolved", "Dismissed"},
                     "Investigating": {"Resolved", "Dismissed"},
                     "Resolved": {"Open"}, "Dismissed": {"Open"}}


def transition(alert_id: str, to_status: str, user: str, note: str = "", owner: str | None = None) -> None:
    with contextlib.closing(app_db()) as con:
        row = con.execute("select status from alerts where alert_id = ?", [alert_id]).fetchone()
        if row is None:
            raise KeyError(alert_id)
        cur = row[0]
        if to_status != cur and to_status not in VALID_TRANSITIONS.get(cur, set()):
            raise ValueError(f"Cannot move alert from {cur} to {to_status}")
        con.execute("update alerts set status=?, owner=coalesce(?, owner), resolution_note=case when ? <> '' then ? else resolution_note end, "
                    "updated_at=current_timestamp where alert_id=?", [to_status, owner, note, note, alert_id])
        con.execute("insert into alert_events (alert_id, user_name, from_status, to_status, note) values (?,?,?,?,?)",
                    [alert_id, user, cur, to_status, note])
        con.commit()
