"""Database access helpers.

* Analytical warehouse: DuckDB file (bronze/silver/gold/ops schemas). The pipeline
  is the only writer; the app opens short-lived READ-ONLY connections so the
  pipeline can still acquire the write lock between queries.
* Application state (BA workspace, alerts, audit log, saved queries): SQLite via
  the standard library, so transactional writes from the UI never contend with
  the warehouse lock.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Iterator

import duckdb
import pandas as pd

from .config import PROJECT_ROOT, settings


# ----------------------------------------------------------------- warehouse
@contextlib.contextmanager
def warehouse(read_only: bool = False, path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    path = path or settings.warehouse_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if read_only and not path.exists():
        raise FileNotFoundError(
            f"Warehouse not found at {path}. Run the pipeline first: python -m businesspulse.pipeline.runner"
        )
    con = duckdb.connect(str(path), read_only=read_only)
    con.execute("set enable_progress_bar = false")
    try:
        yield con
    finally:
        con.close()


def query_df(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a read-only query against the warehouse and return a DataFrame."""
    with warehouse(read_only=True) as con:
        return con.execute(sql, params or []).df()


def warehouse_exists() -> bool:
    return settings.warehouse_path.exists()


def table_exists(schema: str, table: str) -> bool:
    if not warehouse_exists():
        return False
    df = query_df(
        "select count(*) as n from information_schema.tables where table_schema = ? and table_name = ?",
        [schema, table],
    )
    return int(df.iloc[0, 0]) > 0


def warehouse_version() -> float:
    """mtime of the warehouse file - used as a cache key by the UI."""
    try:
        return settings.warehouse_path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


# ----------------------------------------------------------------- app state
MIGRATIONS_DIR = PROJECT_ROOT / "warehouse" / "migrations"


def app_db() -> sqlite3.Connection:
    settings.app_db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(settings.app_db_path), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    con.execute("pragma journal_mode = wal")
    return con


def migrate_app_db() -> list[str]:
    """Apply app-state migrations (idempotent, tracked in schema_migrations)."""
    applied: list[str] = []
    with contextlib.closing(app_db()) as con:
        con.execute(
            "create table if not exists schema_migrations (version text primary key, applied_at text default current_timestamp)"
        )
        done = {r[0] for r in con.execute("select version from schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("app_*.sql")):
            if f.stem in done:
                continue
            con.executescript(f.read_text(encoding="utf-8"))
            con.execute("insert into schema_migrations(version) values (?)", [f.stem])
            applied.append(f.stem)
        con.commit()
    return applied


def app_query(sql: str, params: list | tuple | None = None) -> pd.DataFrame:
    with contextlib.closing(app_db()) as con:
        return pd.read_sql_query(sql, con, params=params or [])


def app_execute(sql: str, params: list | tuple | None = None) -> int:
    with contextlib.closing(app_db()) as con:
        cur = con.execute(sql, params or [])
        con.commit()
        return cur.lastrowid
