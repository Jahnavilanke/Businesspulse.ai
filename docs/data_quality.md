# Data quality framework

## Layers of defence

1. **Source/schema validation** — workbook sheets and header order (`acquire.validate_workbook`);
   CSV and event contract columns (`contracts/transactions.yml`). Violations fail the batch
   (`status = failed`, message kept) without stopping other batches.
2. **Row-level rules** (`businesspulse/quality/cleaning.py`) on every bronze row. `error`
   rules quarantine the row with *all* failing reasons; `warn` rules are measured only.
3. **Expectation suite** (`quality/expectations/silver_transactions.json`, Great Expectations
   format) on silver: uniqueness, not-null, regex, ranges, value sets, referential integrity
   to the geography seed, revenue = qty × price. Blocking failures stop the pipeline.
4. **Contract gate** — overall quality score must be ≥ `sla.min_quality_score` (0.90).
5. **dbt tests** on the models (48): unique / not_null / relationships / accepted_values,
   custom generic tests (`non_negative`, `not_in_future`, `revenue_equals_qty_x_price`) and
   singular business tests (fact ↔ silver reconciliation, KPI mart ↔ fact reconciliation,
   cancellations negative, one "New" order per customer, cohort month-0 = 100%).
6. **Schema monitoring** — every run snapshots the silver schema and records drift
   (added / removed / type-changed columns) in `ops.schema_snapshots`.
7. **Distribution monitoring** — daily/weekly robust z-scores feed the alerts module.

## Row rules

| Rule | Severity | Dimension | Latest failures |
|---|---|---|---|
| duplicate_line | error | uniqueness | 34,335 |
| non_positive_price | error | validity | 6,152 |
| negative_qty_not_cancellation | error | consistency | 3,457 |
| test_record | error | validity | 17 |
| cancellation_positive_qty | error | consistency | 1 |
| missing_invoice_date / invalid_invoice_date / missing_stock_code / invalid_invoice_format / zero_quantity / invalid_customer_id | error | completeness / validity | 0 |
| missing_customer_id | warn | completeness | 243,007 |
| missing_description | warn | completeness | 4,382 (filled from product master when the row survives) |
| unknown_country | warn | referential | 0 |
| extreme_quantity | warn | validity | 15 |

## Scores

`ops.dq_scores`: completeness / validity / uniqueness / consistency = lowest pass rate of
that dimension's rules; **overall** = share of bronze rows passing every error rule
(latest: 0.962). Exposed on the Data Operations page and as Prometheus metric
`bp_data_quality_score`.

## Quarantine

`data/lake/quarantine/transactions/quarantine.parquet` → dbt `bronze.brz_quarantine_transactions`
keeps the original values, reasons, batch, source file/sheet/row and run id, so every
rejected row is traceable back to its spreadsheet row.
