{{ config(enabled = var('enable_synthetic')) }}
select warehouse_id, warehouse_name, city, country, true as is_synthetic
from {{ lake('synthetic/inventory/warehouses.parquet') }}
