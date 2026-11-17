-- BRONZE: raw source records exactly as ingested (all batches, all methods), plus lineage columns.
select * from {{ lake('bronze/transactions/**/*.parquet') }}
