{#- Reusable SQL business metric definitions. Single source of truth for the
    dbt marts; the Python metric layer (businesspulse/analytics/kpis.py) mirrors
    these expressions and a dbt test reconciles both. -#}

{% macro net_revenue(alias='') -%}
    sum({{ alias }}line_revenue) filter (where {{ alias }}line_type = 'product')
{%- endmacro %}

{% macro gross_sales(alias='') -%}
    sum({{ alias }}line_revenue) filter (where {{ alias }}line_type = 'product' and not {{ alias }}is_cancellation)
{%- endmacro %}

{% macro returns_value(alias='') -%}
    -sum({{ alias }}line_revenue) filter (where {{ alias }}line_type = 'product' and {{ alias }}is_cancellation)
{%- endmacro %}

{% macro order_count(alias='') -%}
    count(distinct {{ alias }}invoice_no) filter (where not {{ alias }}is_cancellation and {{ alias }}line_type = 'product')
{%- endmacro %}

{% macro safe_divide(num, den) -%}
    case when ({{ den }}) = 0 or ({{ den }}) is null then null else ({{ num }}) * 1.0 / ({{ den }}) end
{%- endmacro %}

{% macro date_key(col) -%}
    cast(strftime({{ col }}, '%Y%m%d') as integer)
{%- endmacro %}
