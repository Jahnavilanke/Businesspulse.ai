"""Batch ingestion of transaction files into the bronze zone.

* ``ingest_excel`` - full historical load of the UCI workbook (both sheets).
* ``ingest_csv_drops`` - incremental CSV drops (same contract as the workbook) placed in
  ``data/landing/drops``. Files are de-duplicated by content hash and rows are filtered
  against the source high-watermark, so re-running is idempotent.

Bronze keeps source values untouched (original column names, no cleaning) plus
lineage columns: _batch_id, _source_file, _source_sheet, _source_row, _ingested_at.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import settings
from ..logging_utils import get_logger, log
from ..storage.object_store import ObjectStore
from . import registry
from .acquire import EXPECTED_COLUMNS, uci_xlsx_path

logger = get_logger("ingestion.batch")

SOURCE_NAME = "uci_online_retail_ii"
BRONZE_PREFIX = f"bronze/transactions/source={SOURCE_NAME}"
RAW_DTYPES = {"Invoice": "string", "StockCode": "string", "Description": "string", "Country": "string"}


def _read_sheet(xlsx: Path, sheet: str) -> pd.DataFrame:
    try:
        df = pd.read_excel(xlsx, sheet_name=sheet, engine="calamine", dtype=RAW_DTYPES)
    except (ImportError, ValueError):
        df = pd.read_excel(xlsx, sheet_name=sheet, engine="openpyxl", dtype=RAW_DTYPES)
    return df


def _finalize(df: pd.DataFrame, batch_id: str, source_file: str, sheet: str) -> pd.DataFrame:
    df = df[EXPECTED_COLUMNS].copy()
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"], errors="coerce")
    df["_batch_id"] = batch_id
    df["_source_file"] = source_file
    df["_source_sheet"] = sheet
    df["_source_row"] = range(2, len(df) + 2)  # spreadsheet row number (header = row 1)
    df["_ingested_at"] = registry.now()
    return df


def ingest_excel(con, run_id: str, xlsx: Path | None = None, store: ObjectStore | None = None) -> dict:
    xlsx = xlsx or uci_xlsx_path()
    store = store or ObjectStore()
    content_hash = registry.file_sha256(xlsx)
    existing = registry.find_loaded_batch(con, SOURCE_NAME, content_hash)
    if existing:
        log(logger, 20, "workbook already ingested; skipping (idempotent)", batch_id=existing)
        return {"batch_id": existing, "status": "skipped_duplicate", "rows": 0}

    batch_id = registry.new_batch_id(SOURCE_NAME)
    registry.register_batch(
        con, batch_id=batch_id, source_system="UCI ML Repository", source_name=SOURCE_NAME,
        ingestion_method="excel", source_uri=str(xlsx), content_hash=content_hash, is_synthetic=False,
        status="registered", run_id=run_id, started_at=registry.now(),
    )
    try:
        frames = []
        for sheet in ["Year 2009-2010", "Year 2010-2011"]:
            raw = _read_sheet(xlsx, sheet)
            missing = set(EXPECTED_COLUMNS) - set(raw.columns)
            if missing:
                raise ValueError(f"schema validation failed for sheet {sheet}: missing {missing}")
            frames.append(_finalize(raw, batch_id, xlsx.name, sheet))
        df = pd.concat(frames, ignore_index=True)
        key = f"{BRONZE_PREFIX}/method=excel/{batch_id}.parquet"
        df.to_parquet(store.local_path(key), index=False)
        store.sync_written(key)
        registry.update_batch(
            con, batch_id, status="loaded", row_count=len(df), lake_key=key, finished_at=registry.now(),
            min_event_ts=df["InvoiceDate"].min(), max_event_ts=df["InvoiceDate"].max(),
        )
        hw = registry.get_watermark(con, SOURCE_NAME)
        new_hw = df["InvoiceDate"].max()
        if hw is None or new_hw > hw:
            registry.set_watermark(con, SOURCE_NAME, new_hw)
        log(logger, 20, "excel batch loaded", batch_id=batch_id, rows=len(df))
        return {"batch_id": batch_id, "status": "loaded", "rows": len(df), "lake_key": key}
    except Exception as exc:
        registry.update_batch(con, batch_id, status="failed", message=str(exc)[:500], finished_at=registry.now())
        raise


def drops_dir() -> Path:
    d = settings.landing_dir / "drops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ingest_csv_drops(con, run_id: str, store: ObjectStore | None = None, directory: Path | None = None) -> list[dict]:
    """Incremental CSV ingestion with hash de-dup + high-watermark filtering."""
    store = store or ObjectStore()
    directory = directory or drops_dir()
    results = []
    for path in sorted(directory.glob("*.csv")):
        content_hash = registry.file_sha256(path)
        if registry.find_loaded_batch(con, SOURCE_NAME, content_hash):
            results.append({"file": path.name, "status": "skipped_duplicate", "rows": 0})
            continue
        batch_id = registry.new_batch_id(SOURCE_NAME)
        registry.register_batch(
            con, batch_id=batch_id, source_system="CSV drop", source_name=SOURCE_NAME, ingestion_method="csv",
            source_uri=str(path), content_hash=content_hash, is_synthetic=False, status="registered",
            run_id=run_id, started_at=registry.now(),
        )
        try:
            raw = pd.read_csv(path, dtype=RAW_DTYPES)
            missing = set(EXPECTED_COLUMNS) - set(raw.columns)
            if missing:
                raise ValueError(f"CSV contract violation in {path.name}: missing columns {sorted(missing)}")
            df = _finalize(raw, batch_id, path.name, "csv")
            hw = registry.get_watermark(con, SOURCE_NAME)
            before = len(df)
            if hw is not None:
                df = df[df["InvoiceDate"] > pd.Timestamp(hw)]
            key = f"{BRONZE_PREFIX}/method=csv/{batch_id}.parquet"
            df.to_parquet(store.local_path(key), index=False)
            store.sync_written(key)
            registry.update_batch(
                con, batch_id, status="loaded", row_count=len(df), lake_key=key, finished_at=registry.now(),
                min_event_ts=df["InvoiceDate"].min() if len(df) else None,
                max_event_ts=df["InvoiceDate"].max() if len(df) else None,
                message=f"{before - len(df)} rows at/below watermark ignored",
            )
            if len(df):
                registry.set_watermark(con, SOURCE_NAME, df["InvoiceDate"].max())
            results.append({"file": path.name, "status": "loaded", "rows": len(df), "batch_id": batch_id})
        except Exception as exc:
            registry.update_batch(con, batch_id, status="failed", message=str(exc)[:500], finished_at=registry.now())
            results.append({"file": path.name, "status": "failed", "error": str(exc)})
    return results
