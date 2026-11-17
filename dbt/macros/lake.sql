{#- Read a parquet path inside the data lake (local path or s3:// via httpfs) -#}
{% macro lake(key) -%}
    read_parquet('{{ var("lake_dir") }}/{{ key }}', union_by_name = true)
{%- endmacro %}
