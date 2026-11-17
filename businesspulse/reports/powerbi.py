"""Export the gold star schema as Power BI-ready Parquet + CSV files.

Power BI Desktop can import ``data/exports/powerbi/*.parquet`` (Get Data > Parquet) or
connect to PostgreSQL after ``publish_postgres``. See docs/powerbi/README.md.
"""
from __future__ import annotations

import json

from ..config import settings
from ..db import warehouse

STAR_TABLES = ["fact_sales", "fact_orders", "dim_customer", "dim_product", "dim_date", "dim_geography",
               "mart_kpi_monthly", "fact_inventory", "fact_marketing", "dim_campaign"]


def export_star_schema() -> dict:
    out = settings.exports_dir / "powerbi"
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    with warehouse(read_only=True) as con:
        existing = {r[0] for r in con.execute(
            "select table_name from information_schema.tables where table_schema = 'gold'").fetchall()}
        for t in STAR_TABLES:
            if t not in existing:
                continue
            p = (out / f"{t}.parquet").as_posix()
            con.execute(f"copy gold.{t} to '{p}' (format parquet)")
            if t not in {"fact_sales"}:          # CSV for small tables (fact_sales is ~1M rows -> parquet only)
                con.execute(f"copy gold.{t} to '{(out / f'{t}.csv').as_posix()}' (header, delimiter ',')")
            written[t] = con.execute(f"select count(*) from gold.{t}").fetchone()[0]
    (out / "_manifest.json").write_text(json.dumps({"tables": written}, indent=2), encoding="utf-8")
    return {"tables": written, "path": str(out)}
