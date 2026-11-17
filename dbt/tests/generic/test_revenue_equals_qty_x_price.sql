{% test revenue_equals_qty_x_price(model, column_name, quantity_col='quantity', price_col='unit_price') %}
select * from {{ model }}
where abs({{ column_name }} - round({{ quantity_col }} * {{ price_col }}, 2)) > 0.005
{% endtest %}
