"""Bronze -> silver cleaning with row-level validation and quarantine.

Every bronze row gets evaluated against the rules in ``ROW_RULES``. Rows failing any
``error`` rule go to ``quarantine/transactions`` with all failing reasons; the rest
are standardized into ``silver/transactions``. ``warn`` rules are measured and
reported but do not block a row.

All logic runs as DuckDB SQL over parquet, so a full rebuild of ~1M rows takes seconds
and the stage is idempotent (silver is a deterministic function of bronze).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import PROJECT_ROOT, settings
from ..ingestion import registry
from ..logging_utils import get_logger, log
from ..storage.object_store import ObjectStore

logger = get_logger("quality.cleaning")

SEEDS = PROJECT_ROOT / "dbt" / "seeds"


@dataclass(frozen=True)
class RowRule:
    name: str
    severity: str        # error -> quarantine ; warn -> report only
    check_type: str      # completeness | validity | uniqueness | consistency | referential
    column: str
    fail_sql: str        # SQL predicate that is TRUE when the row FAILS
    description: str


ROW_RULES: list[RowRule] = [
    RowRule("missing_invoice_date", "error", "completeness", "InvoiceDate", "InvoiceDate is null",
            "Invoice timestamp missing or unparseable"),
    RowRule("invalid_invoice_date", "error", "validity", "InvoiceDate",
            "InvoiceDate < timestamp '2009-01-01' or InvoiceDate > current_timestamp",
            "Invoice timestamp outside plausible range"),
    RowRule("missing_stock_code", "error", "completeness", "StockCode", "StockCode is null or trim(StockCode) = ''",
            "Product code missing"),
    RowRule("invalid_invoice_format", "error", "validity", "Invoice",
            "Invoice is null or not regexp_matches(Invoice, '^[CA]?[0-9]{6}$')", "Invoice number format invalid"),
    RowRule("zero_quantity", "error", "validity", "Quantity", "Quantity is null or Quantity = 0",
            "Quantity missing or zero"),
    RowRule("negative_qty_not_cancellation", "error", "consistency", "Quantity",
            "Quantity < 0 and not starts_with(Invoice, 'C') and line_type not in ('adjustment','manual','discount','fee')",
            "Negative quantity on a non-cancellation invoice (warehouse stock write-off, not a sale)"),
    RowRule("cancellation_positive_qty", "error", "consistency", "Quantity",
            "starts_with(Invoice, 'C') and Quantity > 0", "Cancellation invoice with positive quantity"),
    RowRule("non_positive_price", "error", "validity", "Price",
            "Price is null or (Price <= 0 and line_type = 'product')", "Product line with zero/negative price"),
    RowRule("invalid_customer_id", "error", "validity", "Customer ID",
            "\"Customer ID\" is not null and (\"Customer ID\" <> floor(\"Customer ID\") or \"Customer ID\" < 10000 or \"Customer ID\" > 99999)",
            "Customer ID present but not a valid 5-digit customer number"),
    RowRule("test_record", "error", "validity", "StockCode", "line_type = 'test'", "Test product, not a business transaction"),
    RowRule("duplicate_line", "error", "uniqueness", "*", "dup_rank > 1",
            "Exact duplicate of another line (all business columns equal). Includes the 2010-12-01..09 overlap "
            "between the two workbook sheets."),
    RowRule("missing_customer_id", "warn", "completeness", "Customer ID", "\"Customer ID\" is null",
            "Guest / unidentified customer - kept for revenue, excluded from customer metrics"),
    RowRule("missing_description", "warn", "completeness", "Description", "Description is null",
            "Description missing - filled from product master where possible"),
    RowRule("unknown_country", "warn", "referential", "Country", "geo_country is null",
            "Country not in geography reference"),
    RowRule("extreme_quantity", "warn", "validity", "Quantity", "abs(Quantity) >= 10000",
            "Unusually large quantity (possible keying error, e.g. 80995 units)"),
]


def _prepared_sql(bronze_glob: str) -> str:
    seeds = str(SEEDS).replace("\\", "/")
    return f"""
    with src as (
        select * from read_parquet('{bronze_glob}', union_by_name = true)
    ),
    np as (select * from read_csv('{seeds}/non_product_codes.csv', header = true, all_varchar = true)),
    geo as (select country_raw from read_csv('{seeds}/country_regions.csv', header = true, all_varchar = true)),
    typed as (
        select src.*,
            case
                when np.line_type is not null then np.line_type
                when upper(src.StockCode) like 'TEST%' then 'test'
                when lower(src.StockCode) like 'gift\\_%' escape '\\' then 'voucher'
                else 'product'
            end as line_type,
            geo.country_raw as geo_country,
            row_number() over (
                partition by src.Invoice, src.StockCode, src.Description, src.Quantity, src.InvoiceDate,
                             src.Price, src."Customer ID", src.Country
                order by src._source_sheet, src._source_row
            ) as dup_rank
        from src
        left join np on upper(trim(src.StockCode)) = upper(np.stock_code)
        left join geo on src.Country = geo.country_raw
    )
    select * from typed
    """


def build_silver(con, run_id: str, store: ObjectStore | None = None) -> dict:
    store = store or ObjectStore()
    bronze_glob = str(store.root / "bronze/transactions/**/*.parquet").replace("\\", "/")
    con.execute(f"create or replace temp view _prepared as {_prepared_sql(bronze_glob)}")

    error_rules = [r for r in ROW_RULES if r.severity == "error"]
    reasons_sql = "list_filter([" + ", ".join(
        f"case when coalesce({r.fail_sql}, false) then '{r.name}' end" for r in error_rules
    ) + "], lambda x: x is not null)"
    con.execute(f"create or replace temp table _flagged as select *, {reasons_sql} as dq_reasons from _prepared")

    total = con.execute("select count(*) from _flagged").fetchone()[0]

    # ---- quarantine
    q_key = "quarantine/transactions/quarantine.parquet"
    con.execute(f"""
        copy (
            select Invoice, StockCode, Description, Quantity, InvoiceDate, Price, "Customer ID", Country,
                   array_to_string(dq_reasons, ',') as dq_reasons, _batch_id, _source_file, _source_sheet,
                   _source_row, '{run_id}' as quarantined_by_run, current_timestamp as quarantined_at
            from _flagged where len(dq_reasons) > 0
        ) to '{store.local_path(q_key).as_posix()}' (format parquet)
    """)
    store.sync_written(q_key)
    quarantined = con.execute("select count(*) from _flagged where len(dq_reasons) > 0").fetchone()[0]

    # ---- silver (standardized)
    s_key = "silver/transactions/transactions.parquet"
    con.execute(f"""
        copy (
            with ok as (select * from _flagged where len(dq_reasons) = 0),
            master as (   -- most frequent non-null description per stock code (product master)
                select StockCode, mode(Description) as master_description
                from ok where Description is not null group by StockCode
            )
            select
                md5(concat_ws('|', ok._source_file, ok._source_sheet, ok._source_row::varchar)) as line_id,
                ok.Invoice as invoice_no,
                upper(trim(ok.StockCode)) as stock_code,
                upper(trim(coalesce(ok.Description, m.master_description, 'UNKNOWN'))) as description,
                ok.Quantity::integer as quantity,
                round(ok.Price, 4)::decimal(12,4) as unit_price,
                round(ok.Quantity * ok.Price, 2)::decimal(14,2) as line_revenue,
                ok.InvoiceDate as invoice_ts,
                cast(ok.InvoiceDate as date) as invoice_date,
                ok."Customer ID"::integer as customer_id,
                ok.Country as country_raw,
                ok.line_type,
                starts_with(ok.Invoice, 'C') as is_cancellation,
                ok._source_sheet as source_sheet,
                ok._batch_id as batch_id,
                ok._ingested_at as ingested_at
            from ok left join master m using (StockCode)
            order by invoice_ts
        ) to '{store.local_path(s_key).as_posix()}' (format parquet)
    """)
    store.sync_written(s_key)
    silver_rows = con.execute(f"select count(*) from read_parquet('{store.local_path(s_key).as_posix()}')").fetchone()[0]

    # ---- record rule results
    checked_at = registry.now()
    rule_rows = []
    for r in ROW_RULES:
        failed = con.execute(f"select count(*) from _prepared where coalesce({r.fail_sql}, false)").fetchone()[0]
        rule_rows.append([run_id, "bronze.transactions", r.name, r.check_type, r.severity, r.column,
                          failed == 0, failed, total, 1 - failed / total if total else 1.0, r.description, checked_at])
    con.executemany("insert into ops.dq_results values (?,?,?,?,?,?,?,?,?,?,?,?)", rule_rows)

    def dim(check_type: str) -> float:
        rows = [x for x in rule_rows if x[3] == check_type]
        return min((x[9] for x in rows), default=1.0)

    scores = {k: dim(k) for k in ["completeness", "validity", "uniqueness", "consistency"]}
    overall = (1 - quarantined / total) if total else 1.0
    con.execute("insert into ops.dq_scores values (?,?,?,?,?,?,?,?)",
                [run_id, "bronze.transactions", scores["completeness"], scores["validity"], scores["uniqueness"],
                 scores["consistency"], overall, checked_at])

    schema = {c[0]: str(c[1]) for c in con.execute(
        f"describe select * from read_parquet('{store.local_path(s_key).as_posix()}')").fetchall()}
    drift = registry.snapshot_schema(con, "silver.transactions", run_id, schema)
    con.execute("drop table if exists _flagged")
    result = {"bronze_rows": total, "silver_rows": silver_rows, "quarantined": quarantined,
              "quality_score": round(overall, 4), "schema_drift": drift, "silver_key": s_key, "quarantine_key": q_key}
    log(logger, 20, "silver built", **result)
    return result
