"""Grounded insight engine.

Pipeline for every question:
  1. route the question to an analysis function,
  2. the function runs real warehouse queries and returns an ``Insight`` holding
     observations (each tied to computed numbers), a supporting-metrics table,
     assumptions, recommendations (clearly labelled as such) and limitations,
  3. a deterministic renderer turns the Insight into text,
  4. OPTIONALLY Claude re-phrases that text (``BP_LLM_PROVIDER=anthropic``). The LLM
     output is accepted only if every number in it appears in the evidence; otherwise
     the deterministic text is used. The LLM never sees raw data and cannot add metrics.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd

from ..analytics import kpis
from ..analytics import root_cause as rc
from ..config import settings
from ..db import app_query, query_df, table_exists
from ..logging_utils import get_logger, log

logger = get_logger("insights")


@dataclass
class Insight:
    question: str
    reporting_period: str
    comparison_period: str | None
    data_basis: str
    observations: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    evidence: pd.DataFrame = field(default_factory=pd.DataFrame)
    narrative: str = ""
    engine: str = "rule-based"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["evidence"] = self.evidence.to_dict(orient="records")
        return d


# ------------------------------------------------------------------ periods
def latest_full_month() -> tuple[date, date]:
    m = query_df("select month from gold.mart_kpi_monthly where not is_partial_month order by month desc limit 1")
    start = pd.Timestamp(m.month[0])
    return start.date(), (start + pd.offsets.MonthEnd(0)).date()


def prev_month(start: date) -> tuple[date, date]:
    s = pd.Timestamp(start) - pd.DateOffset(months=1)
    return s.date(), (s + pd.offsets.MonthEnd(0)).date()


def fmt(v, unit="currency"):
    return kpis.format_value(v, unit)


# ------------------------------------------------------------------ analyses
def revenue_change(period: tuple[date, date] | None = None) -> Insight:
    cur = period or latest_full_month()
    prev = prev_month(cur[0])
    inv = rc.investigate_revenue(cur, prev)
    yoy = kpis.shift_period(*cur, "yoy")
    c, y = kpis.compute_kpis(*cur), None
    if pd.Timestamp(yoy[0]).date() >= kpis.data_range()[0]:
        y = kpis.compute_kpis(*yoy)
    ins = Insight("Why did revenue change in the latest month?", f"{cur[0]} to {cur[1]}", f"{prev[0]} to {prev[1]}",
                  "actual", assumptions=["Net revenue = product lines incl. cancellations; postage/fees excluded.",
                                         "Comparison is the immediately preceding calendar month."])
    ins.observations = [f.observation for f in inv.findings if f.kind == "observation"]
    p = kpis.compute_kpis(*prev)
    if c["net_revenue"] is not None and p["net_revenue"] and c["net_revenue"] >= p["net_revenue"]:
        ins.observations.insert(0, "Net revenue did NOT decline versus the previous month; the decomposition below "
                                   "explains the increase.")
    if y and y["net_revenue"]:
        ins.observations.append(f"Year over year ({yoy[0]} to {yoy[1]}): {fmt(y['net_revenue'])} -> "
                                f"{fmt(c['net_revenue'])} ({(c['net_revenue'] - y['net_revenue']) / y['net_revenue']:+.1%}).")
    ins.recommendations = ["Investigate the largest declining countries/categories with Sales leadership "
                           "(pricing, availability, key-account activity are not in the data)."]
    ins.recommendations += [f.observation for f in inv.findings if f.kind == "hypothesis"]
    ins.limitations = inv.caveats
    ins.evidence = inv.tables["revenue_bridge"]
    return ins


def underperforming_products(period: tuple[date, date] | None = None) -> Insight:
    end = (period or latest_full_month())[1]
    cur = ((pd.Timestamp(end).to_period("M") - 2).start_time.date(), end)   # last 3 full months
    yoy = kpis.shift_period(*cur, "yoy")
    inv = rc.investigate_products(cur, yoy)
    ins = Insight("Which products are underperforming?", f"{cur[0]} to {cur[1]}", f"{yoy[0]} to {yoy[1]} (same period last year)",
                  "actual", assumptions=["Underperforming = net revenue below the same 3 months last year, among products "
                                         "with > £1,000 revenue in the comparison period."])
    ins.observations = [f.observation for f in inv.findings]
    u = inv.tables["underperforming_products"]
    ins.evidence = u[["member", "comparison", "current", "change", "change_pct"]].rename(columns={"member": "product"})
    ins.recommendations = ["Review the listed products for stock availability, price changes or range decisions "
                           "before acting; the data cannot distinguish these causes."]
    ins.limitations = inv.caveats
    return ins


def region_variance(period: tuple[date, date] | None = None) -> Insight:
    cur = period or latest_full_month()
    yoy = kpis.shift_period(*cur, "yoy")
    t = rc.contribution("country", cur, yoy, top=10)
    ins = Insight("Which region has the largest sales variance?", f"{cur[0]} to {cur[1]}", f"{yoy[0]} to {yoy[1]}",
                  "actual", assumptions=["Variance = net revenue vs same period last year (no regional budgets exist "
                                         "in the source; targets can be set in the KPI registry)."])
    if len(t):
        top = t.iloc[0]
        ins.observations.append(f"Largest absolute variance: {top.member} {fmt(top.change)} "
                                f"({fmt(top.comparison)} -> {fmt(top.current)}).")
        uk = t[t.member == "United Kingdom"]
        if len(uk) and t["change"].sum():
            ins.observations.append(f"United Kingdom accounts for {uk.iloc[0].share_of_total_change:.0%} of the total "
                                    "change across the top countries.")
    ins.evidence = t[["member", "comparison", "current", "change", "change_pct", "share_of_total_change"]].rename(
        columns={"member": "country"})
    ins.recommendations = ["Review account-level activity in the highest-variance countries; many customers are "
                           "wholesalers, so a few accounts can drive a country's result."]
    ins.limitations = [rc.CAUSATION_CAVEAT]
    return ins


def churn_risk_segments() -> Insight:
    ins = Insight("Which customer segment has the highest churn risk?", "as of last data date", None, "actual")
    if not table_exists("ml", "churn_scores"):
        ins.limitations = ["Churn model has not been trained yet - run the pipeline with ML enabled."]
        return ins
    t = query_df("""
        select c.rfm_segment, count(*) as customers, avg(s.churn_probability) as avg_churn_probability,
               sum(case when s.risk_band = 'High' then 1 else 0 end) as high_risk_customers,
               sum(c.lifetime_net_revenue) filter (where s.risk_band = 'High') as high_risk_lifetime_revenue
        from ml.churn_scores s join gold.dim_customer c using (customer_key)
        group by 1 order by avg_churn_probability desc
    """)
    mv = query_df("select metric_value from ops.model_runs where model_name='churn' and metric_name='roc_auc' "
                  "order by trained_at desc, metric_value desc limit 1")
    top = t.iloc[0]
    ins.observations = [f"{top.rfm_segment}: average predicted 90-day churn probability {top.avg_churn_probability:.0%} "
                        f"across {top.customers:,.0f} customers ({top.high_risk_customers:,.0f} high-risk)."]
    big = t.sort_values("high_risk_lifetime_revenue", ascending=False).iloc[0]
    ins.observations.append(f"Most historical revenue at high risk sits in {big.rfm_segment} "
                            f"({fmt(big.high_risk_lifetime_revenue)} lifetime revenue).")
    ins.assumptions = ["Churn = no purchase in the next 90 days.",
                       f"Model out-of-time ROC AUC = {mv.metric_value[0]:.2f}." if len(mv) else "Model metrics unavailable."]
    ins.recommendations = ["Prioritise retention outreach to high-risk customers with high historical value; "
                           "measure with a holdout group before scaling."]
    ins.limitations = ["Predictions are probabilities, not certainties; the model reflects 2010-2011 behaviour."]
    ins.evidence = t
    return ins


def best_marketing_channel(period: tuple[date, date] | None = None) -> Insight:
    cur = period or latest_full_month()
    ins = Insight("Which marketing channel has the best conversion rate?", f"{cur[0]} to {cur[1]}", None, "synthetic",
                  limitations=["SYNTHETIC DATA - demonstrates the method only; not the retailer's real marketing."])
    if not kpis.synthetic_available():
        ins.limitations.append("Marketing module disabled (no marketing data available).")
        return ins
    t = query_df("""select channel, sum(clicks) clicks, sum(conversions) conversions,
                           sum(conversions)*1.0/nullif(sum(clicks),0) as conversion_rate,
                           sum(attributed_revenue)/nullif(sum(cost),0) as roas, sum(cost) cost
                    from gold.fact_marketing where activity_date between ? and ? group by 1
                    order by conversion_rate desc""", list(cur))
    top = t.iloc[0]
    ins.observations = [f"{top.channel} had the highest conversion rate ({top.conversion_rate:.2%}, "
                        f"{top.conversions:,.0f} conversions from {top.clicks:,.0f} clicks).",
                        f"Highest ROAS: {t.sort_values('roas', ascending=False).iloc[0].channel} "
                        f"({t.roas.max():.2f}x)."]
    ins.assumptions = ["Conversion rate = conversions / clicks; ROAS = attributed revenue / cost."]
    ins.evidence = t
    return ins


def inventory_shortages() -> Insight:
    ins = Insight("Where are inventory shortages increasing?", "synthetic snapshot", None, "synthetic",
                  limitations=["SYNTHETIC inventory snapshot - illustrates the method only.",
                               "Only one snapshot exists, so a trend ('increasing') cannot be measured; showing the "
                               "current shortage position instead."])
    if not kpis.synthetic_available():
        return ins
    t = query_df("""select warehouse_id, count(*) positions, sum(case when is_stockout then 1 else 0 end) stockouts,
                           sum(case when below_reorder_level then 1 else 0 end) below_reorder
                    from gold.fact_inventory where units_last_90d_actual > 0 group by 1 order by stockouts desc""")
    top = t.iloc[0]
    ins.observations = [f"{top.warehouse_id} has the most stocked-out actively selling positions "
                        f"({top.stockouts:,.0f} of {top.positions:,.0f})."]
    ins.evidence = t
    return ins


def kpis_below_target() -> Insight:
    cur = latest_full_month()
    vals = kpis.compute_kpis(*cur)
    reg = app_query("select kpi_id, name, target_value, direction, unit, data_basis from kpi_registry "
                    "where target_value is not null")
    ins = Insight("Which business KPIs are below target?", f"{cur[0]} to {cur[1]}", None, "actual",
                  assumptions=["Targets come from the Business Analyst KPI registry (user-configurable)."])
    rows = []
    for _, r in reg.iterrows():
        a = vals.get(r.kpi_id)
        if a is None:
            continue
        gap = (a - r.target_value) / abs(r.target_value) if r.target_value else None
        below = a < r.target_value if r.direction == "higher_is_better" else a > r.target_value
        rows.append({"kpi": r["name"], "actual": a, "target": r.target_value, "variance_pct": gap,
                     "status": "Below target" if below else "On / above target", "data_basis": r.data_basis})
    t = pd.DataFrame(rows)
    if len(t):
        bad = t[t.status == "Below target"]
        ins.observations = [f"{len(bad)} of {len(t)} KPIs with targets are off target."] + [
            f"{r.kpi}: {r.actual:,.4g} vs target {r.target:,.4g} ({r.variance_pct:+.1%})"
            + (" [synthetic]" if r.data_basis == "synthetic" else "") for r in bad.itertuples()]
    ins.evidence = t
    return ins


QUESTIONS = {
    "Why did revenue decline this month?": revenue_change,
    "Which products are underperforming?": underperforming_products,
    "Which region has the largest sales variance?": region_variance,
    "Which customer segment has the highest churn risk?": churn_risk_segments,
    "Which marketing channel has the best conversion rate?": best_marketing_channel,
    "Where are inventory shortages increasing?": inventory_shortages,
    "Which business KPIs are below target?": kpis_below_target,
}
ROUTES = [(r"revenue|sales (drop|declin|fell)", revenue_change), (r"product|sku|categor", underperforming_products),
          (r"region|country|geograph", region_variance), (r"churn|retain|retention|segment", churn_risk_segments),
          (r"marketing|channel|campaign|conversion|roas", best_marketing_channel),
          (r"inventory|stock|shortage", inventory_shortages), (r"kpi|target", kpis_below_target)]


def route(question: str):
    if question in QUESTIONS:
        return QUESTIONS[question]
    q = question.lower()
    for pattern, fn in ROUTES:
        if re.search(pattern, q):
            return fn
    return None


# ------------------------------------------------------------------ rendering
def render(ins: Insight) -> str:
    lines = [f"**{ins.question}**", f"Reporting period: {ins.reporting_period}"
             + (f" (compared with {ins.comparison_period})" if ins.comparison_period else "")
             + f" · Data basis: {ins.data_basis}", "", "**Observations (from data)**"]
    lines += [f"- {o}" for o in ins.observations] or ["- No observations could be computed."]
    if ins.recommendations:
        lines += ["", "**Recommendations (judgement - validate before acting)**"] + [f"- {r}" for r in ins.recommendations]
    if ins.assumptions:
        lines += ["", "**Assumptions**"] + [f"- {a}" for a in ins.assumptions]
    if ins.limitations:
        lines += ["", "**Limitations**"] + [f"- {l}" for l in ins.limitations]
    return "\n".join(lines)


_NUM = re.compile(r"\d[\d,]*\.?\d*")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text)}


def grounded(candidate: str, source: str) -> bool:
    """Every number in the LLM text must appear in the deterministic evidence text."""
    return _numbers(candidate) <= _numbers(source)


def llm_rephrase(text: str) -> str | None:
    """Optional: Claude rewrites the verified text for an executive reader. Returns None if
    disabled, unavailable, refused, or if the output introduces any number not in the evidence."""
    if (settings.llm_provider or "").lower() != "anthropic":
        return None
    try:
        import anthropic
    except ImportError:
        log(logger, 30, "BP_LLM_PROVIDER=anthropic but the `anthropic` package is not installed")
        return None
    system = ("You rewrite verified analytics findings for business executives. Use ONLY the facts and numbers given. "
              "Do not add numbers, metrics, causes or recommendations that are not in the input. Keep the section "
              "headings (Observations / Recommendations / Assumptions / Limitations) and the reporting period. "
              "Never present a correlation or decomposition as a proven cause. Output markdown only.")
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=settings.llm_model, max_tokens=16000, system=system,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": text}],
        )
    except anthropic.AuthenticationError:
        log(logger, 30, "LLM authentication failed; using rule-based engine")
        return None
    except anthropic.RateLimitError:
        log(logger, 30, "LLM rate limited; using rule-based engine")
        return None
    except anthropic.APIStatusError as exc:
        log(logger, 30, "LLM API error; using rule-based engine", status=exc.status_code)
        return None
    except anthropic.APIConnectionError:
        log(logger, 30, "LLM unreachable; using rule-based engine")
        return None
    if resp.stop_reason == "refusal":
        return None
    out = "".join(b.text for b in resp.content if b.type == "text").strip()
    if not out or not grounded(out, text):
        log(logger, 30, "LLM output rejected: contained numbers not present in evidence")
        return None
    return out


def answer(question: str, period: tuple[date, date] | None = None) -> Insight:
    fn = route(question)
    if fn is None:
        return Insight(question, "n/a", None, "n/a", limitations=[
            "This question is not supported by a verified analysis. Supported topics: revenue, products, regions, "
            "churn, marketing channels, inventory, KPI targets. Use the SQL Studio for ad-hoc analysis."])
    try:
        ins = fn(period) if "period" in fn.__code__.co_varnames[:fn.__code__.co_argcount] else fn()
    except Exception as exc:
        log(logger, 40, "insight analysis failed", question=question, error=str(exc))
        return Insight(question, "n/a", None, "n/a", limitations=[f"Analysis failed: {exc}"])
    ins.narrative = render(ins)
    llm = llm_rephrase(ins.narrative)
    if llm:
        ins.narrative, ins.engine = llm, f"llm:{settings.llm_model} (grounding-checked)"
    return ins


def evidence_json(ins: Insight) -> str:
    return json.dumps(ins.to_dict(), default=str, indent=2)
