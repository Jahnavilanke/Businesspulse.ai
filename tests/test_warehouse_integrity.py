"""Reconciliation tests on the real built warehouse: metric layer vs independent computation."""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
import pytest

from businesspulse.config import settings
from businesspulse.db import table_exists, warehouse_exists

pytestmark = pytest.mark.skipif(not (warehouse_exists() and table_exists("gold", "fact_sales")),
                                reason="warehouse not built - run the pipeline first")
NOV = (date(2011, 11, 1), date(2011, 11, 30))


def _silver_df():
    p = (settings.lake_dir / "silver/transactions/transactions.parquet").as_posix()
    return duckdb.connect().execute(f"select * from read_parquet('{p}')").df()


def test_kpis_match_independent_pandas_computation():
    from businesspulse.analytics.kpis import compute_kpis
    s = _silver_df()
    d = pd.to_datetime(s.invoice_date)
    s = s[(d >= pd.Timestamp(NOV[0])) & (d <= pd.Timestamp(NOV[1])) & (s.line_type == "product")]
    k = compute_kpis(*NOV)
    assert k["net_revenue"] == pytest.approx(float(s.line_revenue.astype(float).sum()), abs=0.01)
    gross = float(s.loc[~s.is_cancellation, "line_revenue"].astype(float).sum())
    assert k["gross_sales"] == pytest.approx(gross, abs=0.01)
    assert k["orders"] == s.loc[~s.is_cancellation, "invoice_no"].nunique()
    assert k["avg_order_value"] == pytest.approx(gross / k["orders"])
    assert k["active_customers"] == s.loc[~s.is_cancellation & s.customer_id.notna(), "customer_id"].nunique()
    assert k["gross_profit"] is None                         # unavailable, never fabricated


def test_monthly_mart_matches_metric_layer():
    from businesspulse.analytics.kpis import compute_kpis
    from businesspulse.db import query_df
    m = query_df("select * from gold.mart_kpi_monthly where year_month = '2011-11'").iloc[0]
    k = compute_kpis(*NOV)
    assert m.net_revenue == pytest.approx(k["net_revenue"], abs=0.01)
    assert m.orders == k["orders"]
    assert m.new_customers == k["new_customers"]


def test_revenue_bridge_components_sum_exactly():
    from businesspulse.analytics.root_cause import revenue_bridge
    b = revenue_bridge(NOV, (date(2011, 10, 1), date(2011, 10, 31)))
    start, effects, end = b.value.iloc[0], b.value.iloc[1:4].sum(), b.value.iloc[4]
    assert start + effects == pytest.approx(end, abs=0.01)


def test_contribution_shares_sum_to_one():
    from businesspulse.analytics.root_cause import contribution
    t = contribution("region", NOV, (date(2010, 11, 1), date(2010, 11, 30)), top=50)
    assert t.share_of_total_change.sum() == pytest.approx(1.0)


def test_synthetic_tables_are_flagged():
    from businesspulse.db import query_df
    for t in ["fact_inventory", "fact_marketing", "dim_campaign"]:
        if table_exists("gold", t):
            assert query_df(f"select bool_and(is_synthetic) ok from gold.{t}").ok[0]


def test_every_insight_question_answers_with_period_and_basis():
    from businesspulse.insights.engine import QUESTIONS, answer
    for q in QUESTIONS:
        ins = answer(q)
        assert ins.reporting_period and ins.reporting_period != "n/a", q
        assert ins.data_basis in {"actual", "synthetic"}, q
        assert "Observations" in ins.narrative
