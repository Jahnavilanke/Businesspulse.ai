"""Automated dataset acquisition with provenance + integrity validation.

Downloads UCI Online Retail II (if not already present), extracts it, validates the
workbook structure, and writes a provenance manifest next to the file.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

from ..config import PROJECT_ROOT, settings
from ..logging_utils import get_logger, log
from .registry import file_sha256

logger = get_logger("acquire")

UCI_DIR_NAME = "uci_online_retail_ii"
UCI_XLSX_NAME = "online_retail_II.xlsx"
EXPECTED_SHEETS = {"Year 2009-2010", "Year 2010-2011"}
EXPECTED_COLUMNS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]


def load_source_catalog() -> dict:
    with open(PROJECT_ROOT / "config" / "sources.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def uci_dir() -> Path:
    return settings.landing_dir / UCI_DIR_NAME


def uci_xlsx_path() -> Path:
    return uci_dir() / UCI_XLSX_NAME


def download_uci(force: bool = False, timeout: int = 300) -> Path:
    """Download + extract the dataset. Idempotent: skips when the workbook exists."""
    target_dir = uci_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    xlsx = uci_xlsx_path()
    if xlsx.exists() and not force:
        log(logger, 20, "UCI workbook already present; skipping download", path=str(xlsx))
        return xlsx
    zip_path = target_dir / "online_retail_ii.zip"
    log(logger, 20, "downloading UCI Online Retail II", url=settings.uci_dataset_url)
    with requests.get(settings.uci_dataset_url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    with zipfile.ZipFile(zip_path) as zf:
        members = [m for m in zf.namelist() if m.lower().endswith(".xlsx")]
        if not members:
            raise ValueError("Downloaded archive does not contain an .xlsx workbook")
        # Extract only the workbook, by basename, to avoid path traversal.
        with zf.open(members[0]) as src, open(xlsx, "wb") as dst:
            dst.write(src.read())
    return xlsx


def validate_workbook(xlsx: Path) -> dict:
    """Structural validation before ingestion: sheets + header columns."""
    import openpyxl

    wb = openpyxl.load_workbook(xlsx, read_only=True)
    try:
        sheets = set(wb.sheetnames)
        missing = EXPECTED_SHEETS - sheets
        if missing:
            raise ValueError(f"Workbook missing expected sheets: {sorted(missing)}")
        headers = {}
        for name in EXPECTED_SHEETS:
            row = next(wb[name].iter_rows(min_row=1, max_row=1, values_only=True))
            header = [str(c) for c in row if c is not None]
            if header != EXPECTED_COLUMNS:
                raise ValueError(f"Sheet {name!r} header mismatch: {header}")
            headers[name] = header
    finally:
        wb.close()
    return {"sheets": sorted(sheets), "headers": headers}


def write_manifest(xlsx: Path, validation: dict) -> Path:
    catalog = load_source_catalog()["uci_online_retail_ii"]
    manifest = {
        "source_name": "uci_online_retail_ii",
        "provenance": {k: catalog[k] for k in ["title", "provider", "url", "doi", "creator", "license"]},
        "file": xlsx.name,
        "sha256": file_sha256(xlsx),
        "size_bytes": xlsx.stat().st_size,
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "validation": validation,
        "is_synthetic": False,
    }
    path = xlsx.parent / "_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def acquire(force: bool = False) -> dict:
    xlsx = download_uci(force=force)
    validation = validate_workbook(xlsx)
    manifest_path = write_manifest(xlsx, validation)
    log(logger, 20, "UCI dataset acquired and validated", manifest=str(manifest_path))
    return json.loads(manifest_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    print(json.dumps(acquire(), indent=2))
