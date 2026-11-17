"""Object storage abstraction for the data lake.

Local mode writes to ``data/lake`` on the filesystem using S3-style keys
(``bronze/source=uci/...``). S3 mode (AWS S3 or MinIO) mirrors the same keys to a
bucket via boto3. Analytical engines always read a local copy (DuckDB can also
read s3:// directly with the httpfs extension - see docs/cloud_deployment.md).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..config import settings
from ..logging_utils import get_logger, log

logger = get_logger("storage")


class ObjectStore:
    def __init__(self, backend: str | None = None, root: Path | None = None):
        self.backend = backend or settings.storage_backend
        self.root = root or settings.lake_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._s3 = None
        if self.backend == "s3":
            try:
                import boto3  # optional dependency
            except ImportError as exc:  # pragma: no cover - depends on env
                raise RuntimeError("BP_STORAGE_BACKEND=s3 requires `pip install boto3`") from exc
            if not settings.s3_bucket:
                raise RuntimeError("BP_STORAGE_BACKEND=s3 requires BP_S3_BUCKET")
            self._s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint_url)

    def local_path(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def uri(self, key: str) -> str:
        if self.backend == "s3":
            return f"s3://{settings.s3_bucket}/{key}"
        return str(self.root / key)

    def put_file(self, src: Path, key: str) -> str:
        dest = self.local_path(key)
        if Path(src).resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        if self._s3 is not None:
            self._s3.upload_file(str(dest), settings.s3_bucket, key)
        log(logger, 20, "object stored", key=key, backend=self.backend)
        return self.uri(key)

    def sync_written(self, key: str) -> str:
        """Call after writing directly to ``local_path(key)`` to mirror it remotely."""
        if self._s3 is not None:
            self._s3.upload_file(str(self.local_path(key)), settings.s3_bucket, key)
        return self.uri(key)

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file())

    def delete_prefix(self, prefix: str) -> None:
        base = self.root / prefix
        if base.exists():
            shutil.rmtree(base)
