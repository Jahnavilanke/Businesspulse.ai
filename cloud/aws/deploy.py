"""Optional AWS deployment of the gold star schema: S3 -> Glue Data Catalog -> Athena / Redshift.

SAFE BY DEFAULT: runs as a dry run and only prints the actions. Nothing is provisioned
unless you pass --apply. It never creates clusters or other always-on paid resources;
Redshift loading only prints COPY statements for a cluster/workgroup you already own.

    python cloud/aws/deploy.py --bucket my-bucket                 # dry run (prints plan)
    python cloud/aws/deploy.py --bucket my-bucket --apply         # upload + register Glue tables
    python cloud/aws/deploy.py --bucket my-bucket --redshift-sql  # print Redshift COPY script

Requires: pip install boto3, AWS credentials in the standard chain (env / profile / SSO),
and the IAM permissions in cloud/aws/iam_policy.json.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from businesspulse.config import settings  # noqa: E402

TABLES = ["fact_sales", "fact_orders", "dim_customer", "dim_product", "dim_date", "dim_geography", "mart_kpi_monthly"]
GLUE_DB = "businesspulse_gold"
TYPE_MAP = {"VARCHAR": "string", "INTEGER": "int", "BIGINT": "bigint", "DOUBLE": "double", "BOOLEAN": "boolean",
            "DATE": "date", "TIMESTAMP": "timestamp", "FLOAT": "float", "HUGEINT": "bigint"}


def glue_type(t: str) -> str:
    t = t.upper()
    if t.startswith("DECIMAL"):
        return t.lower()
    return TYPE_MAP.get(t, "string")


def schema_of(table: str) -> list[tuple[str, str]]:
    import duckdb
    p = (settings.exports_dir / "powerbi" / f"{table}.parquet").as_posix()
    con = duckdb.connect()
    return [(r[0], r[1]) for r in con.execute(f"describe select * from read_parquet('{p}')").fetchall()]


def plan(bucket: str, prefix: str) -> list[dict]:
    steps = []
    for t in TABLES:
        src = settings.exports_dir / "powerbi" / f"{t}.parquet"
        if not src.exists():
            continue
        steps.append({"table": t, "upload": f"s3://{bucket}/{prefix}/{t}/{t}.parquet", "src": src,
                      "columns": schema_of(t)})
    return steps


def athena_ddl(step: dict, bucket: str, prefix: str) -> str:
    cols = ",\n  ".join(f"`{c}` {glue_type(t)}" for c, t in step["columns"])
    return (f"CREATE EXTERNAL TABLE IF NOT EXISTS {GLUE_DB}.{step['table']} (\n  {cols}\n)\n"
            f"STORED AS PARQUET\nLOCATION 's3://{bucket}/{prefix}/{step['table']}/';")


def redshift_sql(steps: list[dict], bucket: str, prefix: str, iam_role: str) -> str:
    out = ["-- Run in Redshift Serverless / provisioned cluster you already own.", "create schema if not exists gold;"]
    for s in steps:
        cols = ", ".join(f'"{c}" {glue_type(t).replace("string", "varchar(1024)")}' for c, t in s["columns"])
        out.append(f"create table if not exists gold.{s['table']} ({cols});")
        out.append(f"copy gold.{s['table']} from 's3://{bucket}/{prefix}/{s['table']}/' iam_role '{iam_role}' "
                   "format as parquet;")
    return "\n".join(out)


def apply(steps: list[dict], bucket: str, prefix: str, region: str) -> None:  # pragma: no cover - needs AWS
    import boto3

    s3 = boto3.client("s3", region_name=region)
    glue = boto3.client("glue", region_name=region)
    try:
        glue.create_database(DatabaseInput={"Name": GLUE_DB, "Description": "BusinessPulse AI gold star schema"})
    except glue.exceptions.AlreadyExistsException:
        pass
    for s in steps:
        s3.upload_file(str(s["src"]), bucket, f"{prefix}/{s['table']}/{s['table']}.parquet")
        table_input = {
            "Name": s["table"], "TableType": "EXTERNAL_TABLE", "Parameters": {"classification": "parquet"},
            "StorageDescriptor": {
                "Columns": [{"Name": c, "Type": glue_type(t)} for c, t in s["columns"]],
                "Location": f"s3://{bucket}/{prefix}/{s['table']}/",
                "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"}}}
        try:
            glue.create_table(DatabaseName=GLUE_DB, TableInput=table_input)
        except glue.exceptions.AlreadyExistsException:
            glue.update_table(DatabaseName=GLUE_DB, TableInput=table_input)
        print(f"uploaded + registered {s['table']}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bucket", required=True)
    p.add_argument("--prefix", default="businesspulse/gold")
    p.add_argument("--region", default="eu-west-2")
    p.add_argument("--apply", action="store_true", help="actually upload and register (default: dry run)")
    p.add_argument("--redshift-sql", action="store_true")
    p.add_argument("--iam-role", default="arn:aws:iam::<account-id>:role/<redshift-s3-read-role>")
    a = p.parse_args()
    steps = plan(a.bucket, a.prefix)
    if not steps:
        raise SystemExit("No exports found - run the pipeline first (refresh_exports stage).")
    if a.redshift_sql:
        print(redshift_sql(steps, a.bucket, a.prefix, a.iam_role))
        return
    print(f"-- Plan ({'APPLY' if a.apply else 'DRY RUN'}): {len(steps)} tables -> s3://{a.bucket}/{a.prefix}/")
    print(f"CREATE DATABASE IF NOT EXISTS {GLUE_DB};")
    for s in steps:
        print(f"-- upload {s['src'].name} -> {s['upload']}")
        print(athena_ddl(s, a.bucket, a.prefix))
    if a.apply:
        apply(steps, a.bucket, a.prefix, a.region)


if __name__ == "__main__":
    main()
