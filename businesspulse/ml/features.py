"""Point-in-time customer features (no leakage: only data strictly before the cutoff)."""
from __future__ import annotations

import pandas as pd

FEATURES = ["recency_days", "frequency", "monetary", "tenure_days", "avg_order_value", "orders_last_90d",
            "revenue_last_90d", "orders_prev_90d", "distinct_products", "return_ratio", "avg_days_between_orders",
            "is_domestic", "share_q4_revenue"]


def customer_features(con, cutoff: pd.Timestamp) -> pd.DataFrame:
    return con.execute("""
        with o as (
            select * from gold.fact_orders
            where customer_key <> -1 and invoice_date < ?::date
        ),
        lines as (
            select customer_key, count(distinct stock_code) as distinct_products
            from gold.fact_sales where customer_key <> -1 and invoice_date < ?::date and line_type = 'product'
              and not is_cancellation
            group by 1
        ),
        agg as (
            select customer_key,
                date_diff('day', max(invoice_date) filter (where not is_cancellation), ?::date) as recency_days,
                count(*) filter (where not is_cancellation) as frequency,
                sum(order_revenue) as monetary,
                date_diff('day', min(invoice_date), ?::date) as tenure_days,
                avg(order_revenue) filter (where not is_cancellation) as avg_order_value,
                count(*) filter (where not is_cancellation and invoice_date >= ?::date - 90) as orders_last_90d,
                coalesce(sum(order_revenue) filter (where invoice_date >= ?::date - 90), 0) as revenue_last_90d,
                count(*) filter (where not is_cancellation and invoice_date >= ?::date - 180
                                 and invoice_date < ?::date - 90) as orders_prev_90d,
                coalesce(-sum(order_revenue) filter (where is_cancellation), 0)
                    / nullif(sum(order_revenue) filter (where not is_cancellation), 0) as return_ratio,
                date_diff('day', min(invoice_date), max(invoice_date)) * 1.0
                    / nullif(count(*) filter (where not is_cancellation) - 1, 0) as avg_days_between_orders,
                coalesce(sum(order_revenue) filter (where month(invoice_date) in (10, 11, 12)), 0)
                    / nullif(sum(order_revenue), 0) as share_q4_revenue
            from o group by 1
        )
        select a.*, coalesce(l.distinct_products, 0) as distinct_products,
               case when c.home_country = 'United Kingdom' then 1 else 0 end as is_domestic
        from agg a
        left join lines l using (customer_key)
        left join gold.dim_customer c using (customer_key)
        where a.frequency > 0
    """, [cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff]).df()


def future_activity(con, cutoff: pd.Timestamp, horizon_days: int) -> pd.DataFrame:
    return con.execute("""
        select customer_key,
               count(*) filter (where not is_cancellation) as future_orders,
               coalesce(sum(order_revenue), 0) as future_revenue
        from gold.fact_orders
        where customer_key <> -1 and invoice_date >= ?::date and invoice_date < ?::date + ?
        group by 1
    """, [cutoff, cutoff, horizon_days]).df()
