"""Ingestion batch registry and pipeline run metadata (DuckDB ``ops`` schema)."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

OPS_DDL = """
create schema if not exists ops;
create table if not exists ops.ingestion_batches (
    batch_id varchar primary key,
    source_system varchar not null,
    source_name varchar not null,
    ingestion_method varchar not null,   -- excel | csv | api | kafka | synthetic
    source_uri varchar,
    content_hash varchar,
    is_synthetic boolean not null default false,
    row_count bigint,
    min_event_ts timestamp,
    max_event_ts timestamp,
    status varchar not null,             -- registered | loaded | skipped_duplicate | failed
    lake_key varchar,
    run_id varchar,
    started_at timestamp,
    finished_at timestamp,
    message varchar
);
create table if not exists ops.pipeline_runs (
    run_id varchar primary key,
    pipeline varchar not null,
    trigger varchar,
    mode varchar,                        -- full | incremental | backfill
    window_start date,
    window_end date,
    status varchar not null,             -- running | success | failed | partial
    started_at timestamp not null,
    finished_at timestamp,
    duration_s double,
    message varchar
);
create table if not exists ops.task_runs (
    run_id varchar,
    task varchar,
    attempt integer,
    status varchar,
    started_at timestamp,
    finished_at timestamp,
    duration_s double,
    rows_in bigint,
    rows_out bigint,
    message varchar
);
create table if not exists ops.dq_results (
    run_id varchar,
    dataset varchar,
    check_name varchar,
    check_type varchar,
    severity varchar,                    -- error (quarantine) | warn
    column_name varchar,
    passed boolean,
    failed_rows bigint,
    total_rows bigint,
    pass_rate double,
    detail varchar,
    checked_at timestamp
);
create table if not exists ops.dq_scores (
    run_id varchar,
    dataset varchar,
    completeness double,
    validity double,
    uniqueness double,
    consistency double,
    overall double,
    checked_at timestamp
);
create table if not exists ops.schema_snapshots (
    dataset varchar,
    run_id varchar,
    schema_json varchar,
    schema_hash varchar,
    captured_at timestamp,
    drift varchar
);
create table if not exists ops.watermarks (
    source_name varchar primary key,
    high_watermark timestamp,
    updated_at timestamp
);
create table if not exists ops.model_runs (
    run_id varchar,
    model_name varchar,
    model_variant varchar,
    task varchar,
    metric_name varchar,
    metric_value double,
    validation varchar,
    params_json varchar,
    trained_at timestamp,
    mlflow_run_id varchar
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_ops(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(OPS_DDL)


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def new_batch_id(source_name: str) -> str:
    return f"{source_name}-{now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"


def find_loaded_batch(con, source_name: str, content_hash: str) -> str | None:
    row = con.execute(
        "select batch_id from ops.ingestion_batches where source_name = ? and content_hash = ? and status = 'loaded'",
        [source_name, content_hash],
    ).fetchone()
    return row[0] if row else None


def register_batch(con, **fields) -> None:
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    con.execute(f"insert into ops.ingestion_batches ({cols}) values ({marks})", list(fields.values()))


def update_batch(con, batch_id: str, **fields) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"update ops.ingestion_batches set {sets} where batch_id = ?", [*fields.values(), batch_id])


def get_watermark(con, source_name: str):
    row = con.execute("select high_watermark from ops.watermarks where source_name = ?", [source_name]).fetchone()
    return row[0] if row else None


def set_watermark(con, source_name: str, value) -> None:
    con.execute(
        "insert or replace into ops.watermarks values (?, ?, ?)", [source_name, value, now()]
    )


def snapshot_schema(con, dataset: str, run_id: str, schema: dict[str, str]) -> str:
    """Record the schema and compare with the previous snapshot (schema monitoring)."""
    schema_json = json.dumps(schema, sort_keys=True)
    schema_hash = hashlib.md5(schema_json.encode()).hexdigest()
    prev = con.execute(
        "select schema_json from ops.schema_snapshots where dataset = ? order by captured_at desc limit 1", [dataset]
    ).fetchone()
    drift = "none"
    if prev:
        old = json.loads(prev[0])
        added = sorted(set(schema) - set(old))
        removed = sorted(set(old) - set(schema))
        changed = sorted(c for c in set(schema) & set(old) if schema[c] != old[c])
        if added or removed or changed:
            drift = json.dumps({"added": added, "removed": removed, "type_changed": changed})
    else:
        drift = "baseline"
    con.execute(
        "insert into ops.schema_snapshots values (?, ?, ?, ?, ?, ?)",
        [dataset, run_id, schema_json, schema_hash, now(), drift],
    )
    return drift
