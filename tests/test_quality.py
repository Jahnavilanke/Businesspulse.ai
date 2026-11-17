"""Row-level validation, quarantine, standardization and expectation suite."""
from __future__ import annotations

import pandas as pd

from businesspulse.quality.cleaning import build_silver
from businesspulse.quality.expectations import validate
from conftest import raw_rows, write_bronze


def _silver(con, lake):
    return con.execute(f"select * from read_parquet('{lake.local_path('silver/transactions/transactions.parquet').as_posix()}')").df()


def _quarantine(con, lake):
    return con.execute(f"select * from read_parquet('{lake.local_path('quarantine/transactions/quarantine.parquet').as_posix()}')").df()


def test_each_rule_quarantines_expected_rows(con, lake):
    rows = [
        {},                                                     # valid
        {"Invoice": "500002", "Quantity": 0},                   # zero_quantity
        {"Invoice": "500003", "Price": 0.0},                    # non_positive_price
        {"Invoice": "500004", "Quantity": -5},                  # negative qty without cancellation
        {"Invoice": "C500005", "Quantity": 3},                  # cancellation with positive qty
        {"Invoice": "50X006"},                                  # invalid invoice format
        {"Invoice": "500007", "Customer ID": 123.0},            # invalid customer id
        {"Invoice": "500008", "StockCode": "TEST001"},          # test record
        {"Invoice": "500009", "InvoiceDate": pd.NaT},           # missing date
        {"Invoice": "C500010", "Quantity": -6},                 # valid cancellation
        {"Invoice": "500011", "StockCode": "POST", "Description": "POSTAGE", "Price": 18.0, "Quantity": 1},  # postage
        {"Invoice": "500012", "Customer ID": None},             # guest - kept (warn only)
    ]
    df = raw_rows(rows)
    df = pd.concat([df, raw_rows([{}])])                        # exact duplicate of the first row
    write_bronze(lake, df)
    res = build_silver(con, "t1", store=lake)
    assert res["bronze_rows"] == 13
    q = _quarantine(con, lake)
    reasons = {r for x in q.dq_reasons for r in x.split(",")}
    assert {"zero_quantity", "non_positive_price", "negative_qty_not_cancellation", "cancellation_positive_qty",
            "invalid_invoice_format", "invalid_customer_id", "test_record", "missing_invoice_date",
            "duplicate_line"} <= reasons
    s = _silver(con, lake)
    assert res["silver_rows"] == len(s) == 4                    # valid, cancellation, postage, guest
    assert set(s.invoice_no) == {"500001", "C500010", "500011", "500012"}
    assert s.loc[s.invoice_no == "500011", "line_type"].iloc[0] == "postage"
    assert bool(s.loc[s.invoice_no == "C500010", "is_cancellation"].iloc[0])
    # revenue consistency: line_revenue == round(qty * price, 2)
    assert ((s.line_revenue.astype(float) - (s.quantity * s.unit_price.astype(float)).round(2)).abs() < 0.005).all()
    # the duplicate keeps exactly one copy
    assert (s.invoice_no == "500001").sum() == 1


def test_missing_description_filled_from_product_master(con, lake):
    df = raw_rows([{}, {"Invoice": "500002", "Description": None}])
    write_bronze(lake, df)
    build_silver(con, "t2", store=lake)
    s = _silver(con, lake)
    assert (s.description == "WHITE HANGING HEART T-LIGHT HOLDER").all()


def test_idempotent_rebuild(con, lake):
    write_bronze(lake, raw_rows([{}, {"Invoice": "500002"}]))
    a = build_silver(con, "r1", store=lake)
    b = build_silver(con, "r2", store=lake)
    assert a["silver_rows"] == b["silver_rows"]
    assert b["schema_drift"] == "none"                          # second snapshot identical to the first


def test_expectation_suite_passes_on_clean_silver(con, lake):
    write_bronze(lake, raw_rows([{}, {"Invoice": "C500002", "Quantity": -1}, {"Invoice": "500003", "Customer ID": None}]))
    build_silver(con, "t3", store=lake)
    rel = f"read_parquet('{lake.local_path('silver/transactions/transactions.parquet').as_posix()}')"
    res = validate(con, "silver_transactions", rel, "t3", "silver.transactions")
    failed = [r for r in res["results"] if not r["passed"] and r["severity"] == "error"]
    assert res["blocking_failures"] == 0, failed


def test_quality_scores_recorded(con, lake):
    write_bronze(lake, raw_rows([{}, {"Invoice": "500002", "Quantity": 0}]))
    build_silver(con, "t4", store=lake)
    overall = con.execute("select overall from ops.dq_scores where run_id = 't4'").fetchone()[0]
    assert overall == 0.5
