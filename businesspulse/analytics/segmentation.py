"""Customer segmentation: rule-based RFM (from dim_customer) + K-Means clustering."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..db import query_df


def rfm_table() -> pd.DataFrame:
    return query_df("""
        select customer_key, home_country, recency_days, lifetime_orders as frequency,
               lifetime_net_revenue as monetary, rfm_segment, loyalty_tier, registration_date
        from gold.dim_customer where customer_key <> -1 and lifetime_orders > 0
    """)


def rfm_summary() -> pd.DataFrame:
    df = rfm_table()
    g = df.groupby("rfm_segment").agg(customers=("customer_key", "count"), avg_recency=("recency_days", "mean"),
                                      avg_frequency=("frequency", "mean"), avg_monetary=("monetary", "mean"),
                                      total_revenue=("monetary", "sum"))
    g["revenue_share"] = g["total_revenue"] / g["total_revenue"].sum()
    return g.sort_values("total_revenue", ascending=False).reset_index()


def kmeans_segments(k_range=range(3, 8), seed: int = 42) -> dict:
    """Cluster on log-scaled RFM; choose k by silhouette score (computed, not assumed)."""
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    df = rfm_table()
    df = df[df["monetary"] > 0].copy()
    X = np.column_stack([np.log1p(df["recency_days"]), np.log1p(df["frequency"]), np.log1p(df["monetary"])])
    Xs = StandardScaler().fit_transform(X)
    scores = {}
    sample = np.random.default_rng(seed).choice(len(Xs), size=min(4000, len(Xs)), replace=False)
    for k in k_range:
        labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(Xs)
        scores[k] = float(silhouette_score(Xs[sample], labels[sample]))
    best_k = max(scores, key=scores.get)
    km = KMeans(n_clusters=best_k, n_init=10, random_state=seed).fit(Xs)
    df["cluster"] = km.labels_
    prof = df.groupby("cluster").agg(customers=("customer_key", "count"), recency_days=("recency_days", "median"),
                                     frequency=("frequency", "median"), monetary=("monetary", "median"),
                                     revenue=("monetary", "sum")).reset_index()
    # Name clusters from their measured profile (relative to overall medians)
    med = df[["recency_days", "frequency", "monetary"]].median()

    def name(r):
        recent = r.recency_days <= med.recency_days
        freq = r.frequency > med.frequency
        val = r.monetary > med.monetary
        if recent and freq and val:
            return "High-value active"
        if recent and not freq:
            return "Recent low-frequency"
        if not recent and (freq or val):
            return "Lapsing valuable"
        if not recent:
            return "Dormant low-value"
        return "Mid-value"
    prof["label"] = prof.apply(name, axis=1)
    prof["revenue_share"] = prof["revenue"] / prof["revenue"].sum()
    return {"k": best_k, "silhouette": scores, "profile": prof, "assignments": df[["customer_key", "cluster"]]}
