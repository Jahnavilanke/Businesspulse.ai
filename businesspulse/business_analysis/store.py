"""Business Analyst workspace persistence (requirements, KPI registry, processes,
improvements, decisions) with audit logging, plus clearly-flagged demo seed records."""
from __future__ import annotations

import contextlib
import json

import pandas as pd

from ..analytics.kpis import METRICS
from ..db import app_db, app_query, migrate_app_db

TABLES = {
    "requirements": ("req_id", ["req_type", "title", "description", "stakeholder", "priority", "owner", "status",
                                "acceptance_criteria", "linked_kpi", "is_demo"]),
    "kpi_registry": ("kpi_id", ["name", "business_definition", "calculation", "unit", "direction", "target_value",
                                "target_basis", "department", "frequency", "data_basis", "updated_by"]),
    "processes": ("process_id", ["name", "department", "owner", "current_state", "steps_json", "bottlenecks", "issues",
                                 "mermaid", "is_demo"]),
    "improvements": ("improvement_id", ["process_id", "title", "proposal", "expected_impact", "impact_metric", "owner",
                                        "status", "target_date", "is_demo"]),
    "decisions": ("decision_id", ["problem", "evidence", "evidence_source", "proposed_action", "expected_outcome", "owner",
                                  "priority", "estimated_cost", "success_metric", "baseline_value", "target_value",
                                  "status", "actual_outcome", "actual_value", "is_demo"]),
}
REQ_TYPES = ["Business", "Functional", "Non-Functional", "Stakeholder", "Objective", "Scope", "Assumption", "Constraint"]
REQ_PREFIX = {"Business": "BR", "Functional": "FR", "Non-Functional": "NFR", "Stakeholder": "SR", "Objective": "OBJ",
              "Scope": "SC", "Assumption": "AS", "Constraint": "CN"}
STATUSES = {"requirements": ["Draft", "Approved", "In Progress", "Done", "Rejected"],
            "improvements": ["Proposed", "Approved", "In Progress", "Implemented", "Rejected"],
            "decisions": ["Proposed", "Approved", "In Progress", "Implemented", "Evaluated", "Rejected"]}


def audit(user: str, role: str, action: str, entity: str, entity_id: str = "", detail: str = "") -> None:
    with contextlib.closing(app_db()) as con:
        con.execute("insert into audit_log (user_name, role, action, entity, entity_id, detail) values (?,?,?,?,?,?)",
                    [user, role, action, entity, entity_id, detail[:2000]])
        con.commit()


def list_records(table: str) -> pd.DataFrame:
    key, _ = TABLES[table]
    return app_query(f"select * from {table} order by {key}")


def next_id(table: str, prefix: str) -> str:
    key, _ = TABLES[table]
    df = app_query(f"select {key} from {table} where {key} like ?", [f"{prefix}-%"])
    nums = [int(x.split("-")[-1]) for x in df[key] if x.split("-")[-1].isdigit()]
    return f"{prefix}-{(max(nums) + 1) if nums else 1:03d}"


def upsert(table: str, record: dict, user: str, role: str) -> str:
    key, cols = TABLES[table]
    rid = record[key]
    values = {c: record.get(c) for c in cols if c in record}
    with contextlib.closing(app_db()) as con:
        exists = con.execute(f"select 1 from {table} where {key} = ?", [rid]).fetchone()
        if exists:
            sets = ", ".join(f"{c} = ?" for c in values) + ", updated_at = current_timestamp"
            con.execute(f"update {table} set {sets} where {key} = ?", [*values.values(), rid])
            action = "update"
        else:
            names = ", ".join([key, *values])
            con.execute(f"insert into {table} ({names}) values ({', '.join('?' * (len(values) + 1))})", [rid, *values.values()])
            action = "create"
        con.commit()
    audit(user, role, action, table, rid, json.dumps(values, default=str))
    return rid


def delete(table: str, rid: str, user: str, role: str) -> None:
    key, _ = TABLES[table]
    with contextlib.closing(app_db()) as con:
        con.execute(f"delete from {table} where {key} = ?", [rid])
        con.commit()
    audit(user, role, "delete", table, rid)


# ------------------------------------------------------------------ seeds
DEFAULT_TARGETS = {  # targets are planning assumptions, documented in target_basis and editable in the UI
    "net_revenue": (900000, "Monthly plan: approx. FY2011 average monthly net revenue + 5% (planning assumption)"),
    "return_rate": (0.02, "Operations tolerance: returns <= 2% of gross sales"),
    "avg_order_value": (480, "Sales plan: AOV >= £480"),
    "orders": (1800, "Monthly order plan"),
    "new_customers": (200, "Acquisition plan: 200 new identified customers / month"),
    "repeat_purchase_rate": (0.80, "CRM plan: >= 80% of active customers are returning"),
    "retention_rate": (0.40, "CRM plan: >= 40% month-over-month retention"),
    "stockout_rate": (0.05, "Supply chain tolerance (synthetic demo)"),
    "roas": (4.0, "Marketing efficiency target (synthetic demo)"),
    "marketing_conversion_rate": (0.03, "Marketing funnel target (synthetic demo)"),
}


def seed_defaults() -> dict:
    """Idempotently seed the KPI registry and a small set of DEMO records (is_demo = 1)."""
    migrate_app_db()
    created = {"kpis": 0, "demo": 0}
    with contextlib.closing(app_db()) as con:
        for m in METRICS.values():
            if con.execute("select 1 from kpi_registry where kpi_id = ?", [m.key]).fetchone():
                continue
            tgt, basis = DEFAULT_TARGETS.get(m.key, (None, None))
            con.execute("""insert into kpi_registry (kpi_id, name, business_definition, calculation, unit, direction,
                           target_value, target_basis, department, frequency, data_basis, updated_by)
                           values (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [m.key, m.name, m.definition, m.calculation, m.unit, m.direction, tgt, basis, m.department,
                         m.frequency, m.data_basis, "system-seed"])
            created["kpis"] += 1
        if con.execute("select count(*) from requirements").fetchone()[0] == 0:
            reqs = [
                ("OBJ-001", "Objective", "Restore net revenue growth", "Return to positive YoY net revenue growth in "
                 "declining categories within two quarters.", "CEO", "Must", "Head of Sales", "Approved",
                 "Net revenue YoY >= 0% for 2 consecutive full months", "net_revenue"),
                ("BR-001", "Business", "Single trusted KPI definitions", "All departments report revenue, orders and "
                 "retention from the same governed definitions.", "CFO", "Must", "BI Lead", "In Progress",
                 "KPI registry approved; all dashboards read gold.mart_kpi_monthly", "net_revenue"),
                ("BR-002", "Business", "Early warning on returns spikes", "Operations is alerted within one day when "
                 "returns exceed normal levels.", "COO", "Should", "Operations Manager", "Approved",
                 "Weekly return-rate alert with evidence; owner assigned within 24h", "return_rate"),
                ("FR-001", "Functional", "Root-cause drill-down", "Users can decompose a revenue change by orders, AOV, "
                 "returns, country, category and customer type.", "Sales Ops", "Must", "BA", "Done",
                 "Bridge components sum exactly to the total change", "net_revenue"),
                ("FR-002", "Functional", "Churn risk list", "CRM can export customers ranked by 90-day churn risk with "
                 "top drivers.", "CRM Lead", "Should", "Data Science", "Done", "CSV export incl. probability and drivers",
                 "retention_rate"),
                ("NFR-001", "Non-Functional", "Data freshness", "Gold tables refreshed daily by 07:00.", "BI Lead", "Must",
                 "Data Engineering", "Approved", "Airflow DAG SLA 07:00; freshness shown on Data Operations", None),
                ("NFR-002", "Non-Functional", "Read-only analytical access", "Ad-hoc SQL cannot modify warehouse data.",
                 "Security", "Must", "Data Engineering", "Done", "SQL Studio uses read-only connection + statement guard",
                 None),
                ("AS-001", "Assumption", "Customer registration = first purchase", "Source has no CRM; first purchase "
                 "date is used as registration date.", "BA", "Must", "BA", "Approved", "Documented in data dictionary", None),
                ("CN-001", "Constraint", "No cost data", "Gross profit cannot be reported from actual data.", "CFO",
                 "Must", "BA", "Approved", "Gross profit KPI shown as unavailable", "gross_profit"),
            ]
            for r in reqs:
                con.execute("""insert into requirements (req_id, req_type, title, description, stakeholder, priority, owner,
                               status, acceptance_criteria, linked_kpi, is_demo) values (?,?,?,?,?,?,?,?,?,?,1)""", r)
            steps = [{"step": "Daily export from order system", "owner": "IT", "duration_hrs": 2, "bottleneck": False},
                     {"step": "Manual spreadsheet consolidation per department", "owner": "Finance", "duration_hrs": 16,
                      "bottleneck": True},
                     {"step": "Reconcile conflicting revenue figures", "owner": "Finance", "duration_hrs": 8,
                      "bottleneck": True},
                     {"step": "Build monthly management pack", "owner": "BI", "duration_hrs": 12, "bottleneck": False},
                     {"step": "Leadership review", "owner": "Exec", "duration_hrs": 2, "bottleneck": False}]
            mermaid = ("flowchart LR\n  A[Order system export] --> B[Dept spreadsheets]\n  B --> C{Revenue figures agree?}\n"
                       "  C -- No --> D[Manual reconciliation]\n  D --> B\n  C -- Yes --> E[Monthly mgmt pack]\n"
                       "  E --> F[Leadership review]\n  style B fill:#fde2e1\n  style D fill:#fde2e1")
            con.execute("""insert into processes (process_id, name, department, owner, current_state, steps_json,
                           bottlenecks, issues, mermaid, is_demo) values (?,?,?,?,?,?,?,?,?,1)""",
                        ["PRC-001", "Monthly performance reporting", "Finance / BI", "BI Lead",
                         "Monthly pack built manually from departmental spreadsheets (illustrative current state).",
                         json.dumps(steps), "Manual consolidation; reconciliation loop",
                         "Inconsistent revenue definitions; ~5 working-day delay", mermaid])
            con.execute("""insert into improvements (improvement_id, process_id, title, proposal, expected_impact,
                           impact_metric, owner, status, target_date, is_demo) values (?,?,?,?,?,?,?,?,?,1)""",
                        ["IMP-001", "PRC-001", "Automate KPI pack from governed warehouse",
                         "Replace spreadsheet consolidation with gold.mart_kpi_monthly + scheduled export",
                         "Remove 24h of manual effort per cycle (estimate from step durations above)",
                         "Reporting cycle hours", "BI Lead", "In Progress", "2012-01-31"])
            con.execute("""insert into decisions (decision_id, problem, evidence, evidence_source, proposed_action,
                           expected_outcome, owner, priority, estimated_cost, success_metric, status, is_demo)
                           values (?,?,?,?,?,?,?,?,?,?,?,1)""",
                        ["DEC-001", "Large cancellation lines distort monthly revenue and returns KPIs",
                         "Returns-spike alerts in weeks with single very large cancellations (see Alerts)",
                         "alerts:returns_spike", "Add order-quantity validation at entry and a review queue for "
                         "orders > 10,000 units", "Fewer erroneous orders/cancellations; more stable return rate",
                         "Operations Manager", "High", 5000, "return_rate", "Proposed"])
            created["demo"] = len(reqs) + 3
        con.commit()
    return created
