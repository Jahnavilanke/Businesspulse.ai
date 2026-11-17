"""Prebuilt data bundle for hosted deployments.

The full pipeline needs a few GB of RAM and ~2-3 minutes, which free app hosts cannot spare.
CI therefore runs the pipeline and publishes the two files the app reads at runtime - the
DuckDB warehouse and the SQLite app state - as one tar.gz on a GitHub Release. On a host
with no local warehouse the app downloads and unpacks that bundle once at start-up.

    python -m businesspulse.bundle pack  dist/businesspulse-data.tar.gz
    python -m businesspulse.bundle fetch [url]
"""
from __future__ import annotations

import argparse
import logging
import tarfile
import tempfile
from pathlib import Path

import requests

from businesspulse.config import settings

log = logging.getLogger(__name__)

# Archive member name -> local path. Nothing else in an archive is ever extracted.
def _members() -> dict[str, Path]:
    return {
        "warehouse/businesspulse.duckdb": settings.warehouse_path,
        "state/app_state.sqlite": settings.app_db_path,
    }


def pack(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for name, path in _members().items():
            if not path.exists():
                raise FileNotFoundError(f"{path} missing - run the pipeline first")
            tar.add(path, arcname=name)
    log.info("bundle packed", extra={"path": str(out), "bytes": out.stat().st_size})
    return out


def fetch(url: str, timeout: int = 300) -> bool:
    """Download and unpack the bundle. Returns True when the warehouse is in place."""
    wanted = _members()
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "bundle.tar.gz"
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with archive.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                target = wanted.get(member.name)
                if target is None or not member.isfile():
                    continue  # ignore anything unexpected (no path traversal possible)
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                part = target.with_suffix(target.suffix + ".part")
                with part.open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                part.replace(target)
    log.info("bundle fetched", extra={"url": url})
    return settings.warehouse_path.exists()


def ensure_data() -> bool:
    """Make sure a warehouse exists, fetching the published bundle if configured."""
    if settings.warehouse_path.exists():
        return True
    if not settings.data_bundle_url or settings.data_bundle_url.lower() == "none":
        return False
    try:
        return fetch(settings.data_bundle_url)
    except Exception:  # noqa: BLE001 - the app shows its normal "run the pipeline" state
        log.exception("bundle fetch failed", extra={"url": settings.data_bundle_url})
        return False


def main() -> None:
    p = argparse.ArgumentParser(description="Pack or fetch the prebuilt data bundle")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pack").add_argument("out", type=Path)
    sub.add_parser("fetch").add_argument("url", nargs="?")
    args = p.parse_args()
    if args.cmd == "pack":
        print(pack(args.out))
    else:
        url = args.url or settings.data_bundle_url
        if not url:
            raise SystemExit("no URL given and BP_DATA_BUNDLE_URL is not set")
        print("ok" if fetch(url) else "failed")


if __name__ == "__main__":
    main()
