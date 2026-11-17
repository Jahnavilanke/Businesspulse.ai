"""Expectation-suite validation for curated datasets.

Suites are stored in Great Expectations JSON format (``quality/expectations``).
If the ``great_expectations`` package is installed and ``BP_USE_GX=1`` the suite
can be evaluated by GX (see ``run_with_gx``); by default a native DuckDB evaluator
executes the same expectation types directly in SQL, which keeps validation fast on
the full dataset and removes a heavy hard dependency.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import PROJECT_ROOT
from ..ingestion import registry
from ..logging_utils import get_logger, log

logger = get_logger("quality.expectations")
SUITE_DIR = PROJECT_ROOT / "quality" / "expectations"
SEEDS = PROJECT_ROOT / "dbt" / "seeds"


def load_suite(name: str) -> dict:
    return json.loads((SUITE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _where(kwargs: dict) -> str:
    cond = kwargs.get("row_condition")
    return f"where {cond}" if cond else ""


def _evaluate(con, relation: str, exp: dict) -> tuple[bool, int, int, str]:
    """Return (passed, failed_rows, total_rows, detail)."""
    et, kw = exp["expectation_type"], exp["kwargs"]
    col = kw.get("column")
    mostly = float(kw.get("mostly", 1.0))
    where = _where(kw)
    total = con.execute(f"select count(*) from {relation} {where}").fetchone()[0]

    if et == "expect_table_columns_to_match_set":
        actual = {r[0] for r in con.execute(f"describe select * from {relation}").fetchall()}
        expected = set(kw["column_set"])
        missing = sorted(expected - actual)
        extra = sorted(actual - expected) if kw.get("exact_match") else []
        ok = not missing and not extra
        return ok, len(missing) + len(extra), len(expected), f"missing={missing} extra={extra}"

    if et == "expect_column_values_to_be_unique":
        dupes = con.execute(
            f"select coalesce(sum(n), 0) from (select count(*) n from {relation} {where} group by {col} having count(*) > 1)"
        ).fetchone()[0]
        return dupes == 0, int(dupes), total, "duplicate key rows"

    if et == "expect_column_values_to_not_be_null":
        failed = con.execute(f"select count(*) from {relation} {where} {'and' if where else 'where'} {col} is null").fetchone()[0]
    elif et == "expect_column_values_to_match_regex":
        failed = con.execute(
            f"select count(*) from {relation} where {col} is not null and not regexp_matches({col}::varchar, '{kw['regex']}')"
            + (f" and ({kw['row_condition']})" if kw.get("row_condition") else "")
        ).fetchone()[0]
    elif et == "expect_column_values_to_be_between":
        preds = []
        if "min_value" in kw:
            v = kw["min_value"]
            preds.append(f"{col} < {repr(v) if isinstance(v, str) else v}")
        if "max_value" in kw:
            v = kw["max_value"]
            preds.append(f"{col} > {repr(v) if isinstance(v, str) else v}")
        pred = " or ".join(preds)
        cond = f"and ({kw['row_condition']})" if kw.get("row_condition") else ""
        failed = con.execute(f"select count(*) from {relation} where {col} is not null and ({pred}) {cond}").fetchone()[0]
    elif et == "expect_column_values_to_be_in_set":
        if "value_set_from_seed" in kw:
            seed, seed_col = kw["value_set_from_seed"].split(".")
            seed_path = (SEEDS / f"{seed}.csv").as_posix()
            values_sql = f"select {seed_col} from read_csv('{seed_path}', header=true, all_varchar=true)"
        else:
            values_sql = "select unnest([" + ", ".join(repr(v) for v in kw["value_set"]) + "])"
        failed = con.execute(
            f"select count(*) from {relation} where {col} is not null and {col}::varchar not in ({values_sql})"
        ).fetchone()[0]
    elif et == "expect_column_pair_values_to_be_equal":
        failed = con.execute(
            f"select count(*) from {relation} where abs(({kw['column_A']}) - ({kw['column_B']})) > 0.005"
        ).fetchone()[0]
    else:
        return False, 0, total, f"unsupported expectation type {et}"

    rate = 1 - failed / total if total else 1.0
    return rate >= mostly, int(failed), int(total), f"pass_rate={rate:.4%} mostly={mostly}"


def validate(con, suite_name: str, relation: str, run_id: str, dataset: str) -> dict:
    suite = load_suite(suite_name)
    results = []
    for exp in suite["expectations"]:
        severity = exp.get("meta", {}).get("severity", "error")
        try:
            passed, failed, total, detail = _evaluate(con, relation, exp)
        except Exception as exc:  # broken expectation must surface, not crash the run
            passed, failed, total, detail = False, 0, 0, f"evaluation error: {exc}"
        name = exp["expectation_type"] + (f"[{exp['kwargs'].get('column')}]" if exp["kwargs"].get("column") else "")
        results.append({"check": name, "severity": severity, "passed": passed, "failed": failed, "total": total,
                        "detail": detail})
        con.execute(
            "insert into ops.dq_results values (?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id, dataset, name, "expectation", severity, exp["kwargs"].get("column"), passed, failed, total,
             (1 - failed / total) if total else (1.0 if passed else 0.0), detail, registry.now()],
        )
    blocking = [r for r in results if not r["passed"] and r["severity"] == "error"]
    summary = {"suite": suite_name, "evaluated": len(results), "failed": sum(not r["passed"] for r in results),
               "blocking_failures": len(blocking), "results": results, "engine": "native-duckdb"}
    log(logger, 20, "expectation suite evaluated", suite=suite_name, failed=summary["failed"],
        blocking=summary["blocking_failures"])
    return summary


def run_with_gx(parquet_path: Path, suite_name: str) -> dict:  # pragma: no cover - optional path
    """Evaluate the same suite with Great Expectations (GX Core 1.x) on a pandas frame.

    Enabled with ``BP_USE_GX=1``. Expectations with non-standard kwargs used by the
    native evaluator (``value_set_from_seed``, SQL expressions) are skipped here.
    """
    if os.environ.get("BP_USE_GX") != "1":
        return {"skipped": "BP_USE_GX not set"}
    import great_expectations as gx
    import pandas as pd

    df = pd.read_parquet(parquet_path)
    context = gx.get_context(mode="ephemeral")
    batch = context.data_sources.add_pandas("bp").add_dataframe_asset("t").add_batch_definition_whole_dataframe(
        "all").get_batch(batch_parameters={"dataframe": df})
    out = []
    for exp in load_suite(suite_name)["expectations"]:
        kw = {k: v for k, v in exp["kwargs"].items() if k != "row_condition"}
        if "value_set_from_seed" in kw or "(" in str(kw.get("column_B", "")):
            continue
        cls_name = "".join(p.capitalize() for p in exp["expectation_type"].split("_"))
        cls = getattr(gx.expectations, cls_name, None)
        if cls is None:
            continue
        res = batch.validate(cls(**kw))
        out.append({"expectation": exp["expectation_type"], "success": bool(res.success)})
    return {"engine": "great_expectations", "results": out}
