"""Pipeline stage functions.

Each stage is a plain function ``(ctx) -> dict`` so the same code runs from the local
runner (``python -m businesspulse.pipeline.runner``) and from Airflow PythonOperators.
Stages open their own short-lived warehouse connection so dbt (a separate process /
connection) can take the DuckDB write lock in between.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date

from ..config import PROJECT_ROOT, settings
from ..db import warehouse
from ..ingestion import registry
from ..logging_utils import get_logger, log
from ..storage.object_store import ObjectStore
from . import lineage

logger = get_logger("pipeline.stages")
SILVER_TX = "silver/transactions/transactions.parquet"


class NonRetryableError(RuntimeError):
    """Deterministic failure (failed tests / contracts): retrying cannot help."""


@dataclass
class RunContext:
    run_id: str
    mode: str = "full"                 # full | incremental | backfill
    window_start: date | None = None
    window_end: date | None = None
    skip_ml: bool = False
    full_refresh: bool = False
    results: dict = field(default_factory=dict)

    @property
    def store(self) -> ObjectStore:
        return ObjectStore()

    @property
    def silver_path(self) -> str:
        return self.store.local_path(SILVER_TX).as_posix()


# ----------------------------------------------------------------- 1-2 extract + schema validation
def extract_sources(ctx: RunContext) -> dict:
    from ..ingestion.acquire import acquire

    manifest = acquire()
    lineage.emit("extract_sources", "COMPLETE", ctx.run_id, inputs=["uci:online_retail_ii.zip"],
                 outputs=["landing:uci_online_retail_ii/online_retail_II.xlsx"])
    return {"sha256": manifest["sha256"], "sheets": manifest["validation"]["sheets"]}


# ----------------------------------------------------------------- 3-4 raw storage + batch registration
def ingest_raw(ctx: RunContext) -> dict:
    from ..ingestion.batch import ingest_csv_drops, ingest_excel

    with warehouse() as con:
        registry.ensure_ops(con)
        excel = ingest_excel(con, ctx.run_id, store=ctx.store)
        drops = ingest_csv_drops(con, ctx.run_id, store=ctx.store)
        lineage.record_edges(con, "ingest_raw", ctx.run_id, ["landing:uci_online_retail_ii/online_retail_II.xlsx",
                                                               "landing:drops/*.csv"], ["lake:bronze/transactions"])
    lineage.emit("ingest_raw", "COMPLETE", ctx.run_id, inputs=["landing:online_retail_II.xlsx", "landing:drops"],
                 outputs=["lake:bronze/transactions"])
    failed = [d for d in drops if d["status"] == "failed"]
    return {"excel": excel, "csv_drops": drops, "failed_files": len(failed)}


def ingest_api(ctx: RunContext) -> dict:
    from ..ingestion.api_fx import ingest_fx

    with warehouse() as con:
        registry.ensure_ops(con)
        result = ingest_fx(con, ctx.run_id, "2009-12-01", "2011-12-31", store=ctx.store)
    if result.get("status") == "loaded":
        lineage.emit("ingest_api_fx", "COMPLETE", ctx.run_id, inputs=["api:frankfurter"], outputs=["lake:silver/fx_rates"])
    return result


# ----------------------------------------------------------------- 5-7 clean, validate, quarantine
def clean_and_validate(ctx: RunContext) -> dict:
    from ..quality.cleaning import build_silver
    from ..quality.expectations import validate

    with warehouse() as con:
        registry.ensure_ops(con)
        silver = build_silver(con, ctx.run_id, store=ctx.store)
        suite = validate(con, "silver_transactions", f"read_parquet('{ctx.silver_path}')", ctx.run_id,
                         "silver.transactions")
        lineage.record_edges(con, "clean_and_validate", ctx.run_id, ["lake:bronze/transactions"],
                             ["lake:silver/transactions", "lake:quarantine/transactions"])
    lineage.emit("clean_and_validate", "COMPLETE", ctx.run_id, inputs=["lake:bronze/transactions"],
                 outputs=["lake:silver/transactions", "lake:quarantine/transactions"],
                 facets={"dataQualityMetrics": {"_producer": lineage.PRODUCER, "_schemaURL": lineage.SCHEMA_URL,
                                                "rowCount": silver["silver_rows"]}})
    if suite["blocking_failures"]:
        raise NonRetryableError(f"silver expectation suite failed: {suite['blocking_failures']} blocking checks")
    # Data-contract gate: the producer contract sets the minimum acceptable quality score.
    import yaml
    contract = yaml.safe_load((PROJECT_ROOT / "contracts" / "transactions.yml").read_text(encoding="utf-8"))
    minimum = float(contract["sla"]["min_quality_score"])
    if silver["quality_score"] < minimum:
        raise NonRetryableError(f"quality score {silver['quality_score']:.3f} below contract minimum {minimum}")
    return {**silver, "contract_min_quality": minimum, "expectations_failed": suite["failed"], "expectations_evaluated": suite["evaluated"]}


def generate_synthetic(ctx: RunContext) -> dict:
    if not settings.enable_synthetic_support:
        return {"status": "disabled"}
    from ..ingestion.synthetic import generate_inventory, generate_marketing

    with warehouse() as con:
        registry.ensure_ops(con)
        inv = generate_inventory(con, ctx.run_id, ctx.silver_path, store=ctx.store)
        mkt = generate_marketing(con, ctx.run_id, ctx.silver_path, store=ctx.store)
    return {"inventory": inv, "marketing": mkt}


# ----------------------------------------------------------------- 8-10 dbt transformations + warehouse load
def dbt_build(ctx: RunContext) -> dict:
    """Run ``dbt build`` (seeds, models, tests) in a subprocess so dbt owns its own DuckDB
    connection and releases the file lock when it exits."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    env = {**os.environ, "BP_WAREHOUSE_PATH": str(settings.warehouse_path), "BP_LAKE_DIR": settings.lake_dir.as_posix(),
           "PYTHONIOENCODING": "utf-8", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    fx_available = (settings.lake_dir / "silver" / "fx_rates" / "fx_monthly.parquet").exists()
    dbt_vars = json.dumps({"enable_synthetic": settings.enable_synthetic_support, "enable_fx": fx_available})
    dbt_dir = PROJECT_ROOT / "dbt"
    exe = shutil.which("dbt", path=str(Path(sys.executable).parent)) or shutil.which("dbt")
    if exe is None:
        raise NonRetryableError("dbt executable not found - pip install dbt-duckdb")
    cmd = [exe, "build", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir), "--vars", dbt_vars,
           "--no-use-colors"]
    if ctx.full_refresh:
        cmd.append("--full-refresh")
    proc = subprocess.run(cmd, cwd=dbt_dir, env=env, capture_output=True, text=True, encoding="utf-8")
    (settings.logs_dir / "dbt_last_run.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    summary: dict = {"success": proc.returncode == 0, "nodes": {}, "failures": []}
    rr = dbt_dir / "target" / "run_results.json"
    if rr.exists():
        for r in json.loads(rr.read_text(encoding="utf-8"))["results"]:
            summary["nodes"][r["status"]] = summary["nodes"].get(r["status"], 0) + 1
            if r["status"] in {"error", "fail"}:
                summary["failures"].append(f"{r['unique_id']}: {(r.get('message') or '')[:200]}")
    with warehouse() as con:
        registry.ensure_ops(con)
        summary["lineage_edges"] = lineage.record_dbt_lineage(con, ctx.run_id)
    lineage.emit("dbt_build", "COMPLETE" if proc.returncode == 0 else "FAIL", ctx.run_id,
                 inputs=["lake:silver/transactions"], outputs=["gold.fact_sales", "gold.mart_kpi_monthly"])
    if proc.returncode != 0:
        tail = (proc.stdout + proc.stderr)[-800:]
        raise NonRetryableError(f"dbt build failed: {summary['failures'][:5] or tail}")
    return summary


# ----------------------------------------------------------------- 11 metrics, ML, alerts
def train_models(ctx: RunContext) -> dict:
    if ctx.skip_ml:
        return {"status": "skipped"}
    from ..ml.train_all import train_all

    return train_all(ctx.run_id)


def detect_alerts(ctx: RunContext) -> dict:
    from ..alerts.detector import run_detection

    return run_detection()


def refresh_exports(ctx: RunContext) -> dict:
    from ..reports.powerbi import export_star_schema

    return export_star_schema()


# ----------------------------------------------------------------- optional publish
def publish_postgres(ctx: RunContext) -> dict:
    """Copy gold tables to PostgreSQL (serving layer for Superset / Power BI) when configured."""
    if not settings.postgres_dsn:
        return {"status": "skipped", "reason": "BP_POSTGRES_DSN not set"}
    with warehouse() as con:
        con.execute("install postgres; load postgres;")
        con.execute(f"attach '{settings.postgres_dsn}' as pg (type postgres)")
        con.execute("create schema if not exists pg.gold")
        tables = [r[0] for r in con.execute(
            "select table_name from information_schema.tables where table_schema = 'gold'").fetchall()]
        for t in tables:
            con.execute(f"create or replace table pg.gold.{t} as select * from gold.{t}")
        con.execute("detach pg")
    return {"status": "published", "tables": len(tables)}


STAGES = [
    ("extract_sources", extract_sources),
    ("ingest_raw", ingest_raw),
    ("ingest_api", ingest_api),
    ("clean_and_validate", clean_and_validate),
    ("generate_synthetic", generate_synthetic),
    ("dbt_build", dbt_build),
    ("train_models", train_models),
    ("detect_alerts", detect_alerts),
    ("refresh_exports", refresh_exports),
    ("publish_postgres", publish_postgres),
]
# Stages whose failure must not fail the whole run (optional sources / serving).
OPTIONAL_STAGES = {"ingest_api", "publish_postgres"}
