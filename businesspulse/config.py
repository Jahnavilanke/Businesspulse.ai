"""Central configuration. Every setting is read from environment variables
(optionally loaded from a `.env` file) so no secrets are hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency). Existing env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str | None = None) -> str | None:
    value = os.environ.get(key)
    return value if value not in (None, "") else default


def _env_bool(key: str, default: bool) -> bool:
    value = _env(key)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _path(key: str, default: Path) -> Path:
    value = _env(key)
    p = Path(value) if value else default
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: _env("BP_ENV", "local"))
    data_dir: Path = field(default_factory=lambda: _path("BP_DATA_DIR", PROJECT_ROOT / "data"))
    warehouse_path: Path = field(
        default_factory=lambda: _path("BP_WAREHOUSE_PATH", PROJECT_ROOT / "data" / "warehouse" / "businesspulse.duckdb")
    )
    app_db_path: Path = field(
        default_factory=lambda: _path("BP_APP_DB_PATH", PROJECT_ROOT / "data" / "state" / "app_state.sqlite")
    )
    # Object storage: "local" (filesystem lake) or "s3" (AWS S3 / MinIO via boto3)
    storage_backend: str = field(default_factory=lambda: _env("BP_STORAGE_BACKEND", "local"))
    s3_bucket: str | None = field(default_factory=lambda: _env("BP_S3_BUCKET"))
    s3_endpoint_url: str | None = field(default_factory=lambda: _env("BP_S3_ENDPOINT_URL"))
    # Source acquisition
    uci_dataset_url: str = field(
        default_factory=lambda: _env(
            "BP_UCI_URL", "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
        )
    )
    # Prebuilt warehouse bundle for hosted deployments (set to 'none' to disable the download)
    data_bundle_url: str | None = field(
        default_factory=lambda: _env(
            "BP_DATA_BUNDLE_URL",
            "https://github.com/Jahnavilanke/Businesspulse.ai/releases/download/data-latest/businesspulse-data.tar.gz",
        )
    )
    enable_synthetic_support: bool = field(default_factory=lambda: _env_bool("BP_ENABLE_SYNTHETIC", True))
    enable_fx_api: bool = field(default_factory=lambda: _env_bool("BP_ENABLE_FX_API", True))
    fx_api_url: str = field(default_factory=lambda: _env("BP_FX_API_URL", "https://api.frankfurter.app"))
    synthetic_seed: int = field(default_factory=lambda: int(_env("BP_SYNTHETIC_SEED", "42")))
    # Optional services
    kafka_bootstrap: str | None = field(default_factory=lambda: _env("BP_KAFKA_BOOTSTRAP"))
    kafka_topic: str = field(default_factory=lambda: _env("BP_KAFKA_TOPIC", "retail.transactions"))
    openlineage_url: str | None = field(default_factory=lambda: _env("OPENLINEAGE_URL"))
    openlineage_namespace: str = field(default_factory=lambda: _env("OPENLINEAGE_NAMESPACE", "businesspulse"))
    mlflow_tracking_uri: str | None = field(default_factory=lambda: _env("MLFLOW_TRACKING_URI"))
    postgres_dsn: str | None = field(default_factory=lambda: _env("BP_POSTGRES_DSN"))
    # Optional LLM (Claude via the `anthropic` SDK) that only re-phrases verified evidence.
    # Never required: unset -> deterministic rule-based insight engine. Credentials come
    # from the SDK's standard resolution (ANTHROPIC_API_KEY etc.), never from code.
    llm_provider: str | None = field(default_factory=lambda: _env("BP_LLM_PROVIDER"))  # "anthropic" to enable
    llm_model: str = field(default_factory=lambda: _env("BP_LLM_MODEL", "claude-opus-5-5"))
    # Security
    auth_mode: str = field(default_factory=lambda: _env("BP_AUTH_MODE", "demo"))  # demo | password
    sql_row_limit: int = field(default_factory=lambda: int(_env("BP_SQL_ROW_LIMIT", "5000")))
    log_level: str = field(default_factory=lambda: _env("BP_LOG_LEVEL", "INFO"))

    # Derived paths -----------------------------------------------------
    @property
    def landing_dir(self) -> Path:
        return self.data_dir / "landing"

    @property
    def lake_dir(self) -> Path:
        return self.data_dir / "lake"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def mlruns_dir(self) -> Path:
        return self.data_dir / "mlruns"

    @property
    def lineage_dir(self) -> Path:
        return self.data_dir / "lineage"

    def ensure_dirs(self) -> None:
        for p in [self.landing_dir, self.lake_dir, self.exports_dir, self.logs_dir, self.lineage_dir,
                  self.warehouse_path.parent, self.app_db_path.parent, self.mlruns_dir]:
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
