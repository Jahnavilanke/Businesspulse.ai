from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from businesspulse.config import settings  # noqa: E402
from businesspulse.ingestion import registry  # noqa: E402
from businesspulse.storage.object_store import ObjectStore  # noqa: E402


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("set enable_progress_bar = false")
    registry.ensure_ops(c)
    yield c
    c.close()


@pytest.fixture
def lake(tmp_path) -> ObjectStore:
    return ObjectStore(backend="local", root=tmp_path / "lake")


@pytest.fixture
def app_db_tmp(tmp_path):
    """Point the app-state database at a temp file for the test."""
    original = settings.app_db_path
    object.__setattr__(settings, "app_db_path", tmp_path / "app.sqlite")
    from businesspulse.db import migrate_app_db
    migrate_app_db()
    yield settings.app_db_path
    object.__setattr__(settings, "app_db_path", original)


def raw_rows(rows: list[dict]) -> pd.DataFrame:
    base = {"Invoice": "500001", "StockCode": "85123A", "Description": "WHITE HANGING HEART T-LIGHT HOLDER",
            "Quantity": 6, "InvoiceDate": pd.Timestamp("2011-01-10 10:00"), "Price": 2.55, "Customer ID": 17850.0,
            "Country": "United Kingdom"}
    df = pd.DataFrame([{**base, **r} for r in rows])
    df["Invoice"] = df["Invoice"].astype("string")
    df["StockCode"] = df["StockCode"].astype("string")
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"])
    return df


def write_bronze(store: ObjectStore, df: pd.DataFrame, name: str = "test") -> None:
    from businesspulse.ingestion.batch import BRONZE_PREFIX, _finalize

    out = _finalize(df, f"batch-{name}", f"{name}.xlsx", "Year 2010-2011")
    out.to_parquet(store.local_path(f"{BRONZE_PREFIX}/method=excel/{name}.parquet"), index=False)
