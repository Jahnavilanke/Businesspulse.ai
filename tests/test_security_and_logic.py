"""SQL guard, RBAC, simulator math, insight grounding, BA store and alert workflow."""
from __future__ import annotations

import pytest

from businesspulse.security import can, can_view, check_sql


@pytest.mark.parametrize("sql", [
    "select * from gold.fact_sales", "with a as (select 1) select * from a", "describe gold.dim_date",
    "select 'drop table x' as s",                     # keyword inside a string literal is fine
    "-- comment\nselect 1",
])
def test_sql_guard_allows_reads(sql):
    assert check_sql(sql)[0], check_sql(sql)


@pytest.mark.parametrize("sql", [
    "drop table gold.fact_sales", "delete from gold.fact_sales", "select 1; drop table x",
    "insert into t values (1)", "attach 'x.db'", "copy gold.fact_sales to 'out.csv'",
    "select * from read_csv('c:/secret.csv')", "with x as (select 1) update t set a = 1", "set threads = 1",
    "select * from read_parquet('s3://bucket/x')", "pragma database_list", "",
])
def test_sql_guard_blocks_writes_and_file_access(sql):
    assert not check_sql(sql)[0]


def test_rbac_matrix():
    assert can_view("Data Analyst", "operations") and not can_view("Business Manager", "operations")
    assert can_view("Business Analyst", "business_analyst") and not can_view("Data Analyst", "business_analyst")
    assert can("Business Manager", "approve_decisions") and not can("Business Analyst", "approve_decisions")
    assert can("Data Analyst", "run_sql") and not can("Business Analyst", "run_sql")
    assert can_view("Administrator", "admin")


BASE = {"period": "x", "net_revenue": 1000.0, "gross_sales": 1100.0, "returns_value": 100.0, "orders": 10.0,
        "units_sold": 500.0, "aov": 110.0, "avg_active_customers": 50.0, "retention_rate": 0.4,
        "revenue_per_active_customer_month": 2.0}


def test_simulator_no_change_reproduces_baseline():
    from businesspulse.simulator.engine import Assumptions, simulate
    r = simulate(Assumptions(), BASE)
    assert r["net_revenue"] == pytest.approx(1000.0)
    assert r["change_vs_baseline"] == pytest.approx(0.0)
    assert "gross_profit" not in r                     # no margin assumption -> no profit claimed


def test_simulator_price_elasticity_math():
    from businesspulse.simulator.engine import Assumptions, simulate
    r = simulate(Assumptions(price_change_pct=10, price_elasticity=-1.0), BASE)
    # price x1.10, volume x0.90 -> gross 1100 * 0.99 ; returns keep their 9.09% rate
    assert r["gross_sales"] == pytest.approx(1100 * 1.1 * 0.9)
    assert r["net_revenue"] == pytest.approx(1100 * 0.99 * (1 - 100 / 1100))
    m = simulate(Assumptions(marketing_budget_change=100, incremental_roas=3), BASE)
    assert m["revenue_from_marketing"] == pytest.approx(300)
    g = simulate(Assumptions(gross_margin_pct=50), BASE)
    assert g["gross_profit"] == pytest.approx(g["baseline_gross_profit"])


def test_insight_grounding_rejects_invented_numbers():
    from businesspulse.insights.engine import grounded
    src = "Net revenue was £1,427,125 vs £1,061,701 (+34.4%). Orders 2,751."
    assert grounded("Revenue rose 34.4% to £1,427,125 on 2,751 orders.", src)
    assert not grounded("Revenue rose 34.4%, driven by a 12% price increase.", src)


def test_ba_store_ids_and_audit(app_db_tmp):
    from businesspulse.business_analysis import store
    from businesspulse.db import app_query
    rid = store.next_id("requirements", "FR")
    store.upsert("requirements", {"req_id": rid, "req_type": "Functional", "title": "T", "priority": "Must",
                                  "status": "Draft", "is_demo": 0}, "u", "Business Analyst")
    assert rid == "FR-001" and store.next_id("requirements", "FR") == "FR-002"
    store.upsert("requirements", {"req_id": rid, "title": "T2", "status": "Approved"}, "u", "Business Analyst")
    row = app_query("select title, status from requirements where req_id = ?", [rid]).iloc[0]
    assert (row.title, row.status) == ("T2", "Approved")
    log = app_query("select action from audit_log order by id")
    assert list(log.action) == ["create", "update"]


def test_alert_workflow_transitions(app_db_tmp):
    from businesspulse.alerts.detector import _alert, transition, upsert
    from businesspulse.db import app_query
    a = _alert("returns_spike", "weekly_return_rate", "W2011-49", "2011-12-09", 0.35, 0.02, "critical", {}, "check")
    assert upsert([a])["new"] == 1
    assert upsert([a]) == {"new": 0, "updated": 1, "total_detected": 1}      # re-detection keeps workflow state
    transition(a["alert_id"], "Acknowledged", "u")
    transition(a["alert_id"], "Investigating", "u", owner="CRM Lead")
    transition(a["alert_id"], "Resolved", "u", "keying error reversed")
    with pytest.raises(ValueError):
        transition(a["alert_id"], "Investigating", "u")                     # Resolved -> Investigating not allowed
    r = app_query("select status, owner, resolution_note from alerts").iloc[0]
    assert (r.status, r.owner, r.resolution_note) == ("Resolved", "CRM Lead", "keying error reversed")
    assert len(app_query("select * from alert_events")) == 4
