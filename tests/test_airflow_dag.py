"""Validate the Airflow DAG structure without an Airflow installation (stubbed operators)."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

DAG_FILE = Path(__file__).resolve().parents[1] / "airflow" / "dags" / "businesspulse_daily.py"


def _stub_airflow(monkeypatch):
    created = {}

    class DAG:
        def __init__(self, dag_id, **kw):
            self.dag_id, self.kw = dag_id, kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class PythonOperator:
        def __init__(self, task_id, python_callable, op_kwargs=None, **kw):
            self.task_id, self.kw, self.downstream = task_id, kw, set()
            created[task_id] = self

        def __rshift__(self, other):
            for o in (other if isinstance(other, list) else [other]):
                self.downstream.add(o.task_id)
            return other

        def __rrshift__(self, others):
            for o in others:
                o.downstream.add(self.task_id)
            return self

    airflow = types.ModuleType("airflow")
    airflow.DAG = DAG
    exc = types.ModuleType("airflow.exceptions")
    exc.AirflowFailException = type("AirflowFailException", (Exception,), {})
    ops = types.ModuleType("airflow.operators")
    py = types.ModuleType("airflow.operators.python")
    py.PythonOperator = PythonOperator
    for name, mod in {"airflow": airflow, "airflow.exceptions": exc, "airflow.operators": ops,
                      "airflow.operators.python": py}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return created


def test_dag_covers_every_stage_in_dependency_order(monkeypatch):
    tasks = _stub_airflow(monkeypatch)
    spec = importlib.util.spec_from_file_location("bp_dag", DAG_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from businesspulse.pipeline.stages import STAGES

    assert set(tasks) == {n for n, _ in STAGES} | {"finish_run"}
    assert mod.dag.kw["max_active_runs"] == 1 and mod.dag.kw["catchup"] is False
    # topological check: every upstream stage precedes its downstream in the runner's order too
    order = [n for n, _ in STAGES]
    for name, t in tasks.items():
        for d in t.downstream:
            if d != "finish_run" and name != "finish_run":
                assert order.index(name) < order.index(d), (name, d)
    assert "dbt_build" in tasks["clean_and_validate"].downstream or \
        "dbt_build" in tasks["generate_synthetic"].downstream
    assert tasks["finish_run"].kw.get("trigger_rule") == "all_done"
    assert tasks["dbt_build"].kw.get("retries") == 0          # deterministic test failures are not retried
