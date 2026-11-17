-- BRONZE: rows rejected by row-level data-quality rules, with the failing rule names.
select * from {{ lake('quarantine/transactions/*.parquet') }}
