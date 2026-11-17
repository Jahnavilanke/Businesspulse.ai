"""SYNTHETIC supporting datasets (inventory + marketing) - DEMONSTRATION ONLY.

No public inventory or marketing data exists for the UCI retailer. These generators
create clearly-labelled, seeded, reproducible datasets so the inventory and marketing
workflows can be demonstrated. Rules followed:

* Every row carries ``is_synthetic = True`` and every batch is registered as synthetic.
* Only legitimate keys are shared with real data: real ``stock_code`` values (the product
  catalogue) and calendar dates. No synthetic value is attached to a real transaction or
  customer, and synthetic attributed revenue is never reconciled against real revenue.
* Inventory is a SINGLE as-of snapshot (no invented history). Stock quantities are scaled
  to real recent demand only so the demo is plausible; the quantities themselves are random.
* Marketing contains two deliberately injected anomalies (documented below) so the alerting
  module has something to detect in the synthetic domain.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import settings
from ..logging_utils import get_logger, log
from ..storage.object_store import ObjectStore
from . import registry

logger = get_logger("ingestion.synthetic")

WAREHOUSES = [
    ("WH-SYN-01", "Synthetic DC North", "Leeds", "United Kingdom"),
    ("WH-SYN-02", "Synthetic DC South", "Reading", "United Kingdom"),
    ("WH-SYN-03", "Synthetic EU Hub", "Rotterdam", "Netherlands"),
]
CHANNELS = {  # channel: (cpm GBP, ctr, cvr, aov GBP, monthly budget GBP) - calibrated to typical retail ROAS 0.5-11x
    "Paid Search": (30.0, 0.030, 0.035, 95.0, 9000),
    "Paid Social": (8.0, 0.010, 0.015, 80.0, 7000),
    "Email": (6.0, 0.030, 0.020, 110.0, 1500),
    "Display": (4.0, 0.003, 0.010, 70.0, 4000),
    "Affiliate": (12.0, 0.020, 0.020, 90.0, 3000),
}
INJECTED_ANOMALIES = [
    "Paid Social spend x3.2 for 7 days from 2011-09-12 with flat conversions (spend anomaly)",
    "Paid Search conversion rate halved from 2011-11-01 for 10 days (conversion-rate drop)",
]


def _register(con, run_id: str, name: str, key: str, n: int, method: str = "synthetic") -> None:
    batch_id = registry.new_batch_id(name)
    registry.register_batch(
        con, batch_id=batch_id, source_system="BusinessPulse synthetic generator", source_name=name,
        ingestion_method=method, source_uri=f"seed={settings.synthetic_seed}", content_hash=f"seed-{settings.synthetic_seed}-{n}",
        is_synthetic=True, row_count=n, status="loaded", lake_key=key, run_id=run_id,
        started_at=registry.now(), finished_at=registry.now(), message="SYNTHETIC - demonstration only",
    )


def generate_inventory(con, run_id: str, silver_path: str, store: ObjectStore | None = None) -> dict:
    store = store or ObjectStore()
    rng = np.random.default_rng(settings.synthetic_seed)
    demand = con.execute(f"""
        with t as (select * from read_parquet('{silver_path}') where line_type = 'product' and not is_cancellation)
        select stock_code,
               sum(quantity) filter (where invoice_date > (select max(invoice_date) from t) - 90) / 90.0 as daily_units,
               max(invoice_date) as last_sold,
               (select max(invoice_date) from t) as as_of
        from t group by stock_code
    """).df()
    as_of = pd.Timestamp(demand["as_of"].iloc[0]).date()
    demand["daily_units"] = demand["daily_units"].fillna(0.0)
    rows = []
    suppliers = [f"SUP-SYN-{i:02d}" for i in range(1, 13)]
    for wh_id, *_ in WAREHOUSES:
        share = 1 / len(WAREHOUSES)                                       # even split of real demand
        cover_days = rng.gamma(shape=2.0, scale=18.0, size=len(demand))       # random days of cover
        stockout = rng.random(len(demand)) < 0.06                              # ~6% stocked out
        wh_daily = demand["daily_units"].to_numpy() * share
        stock = np.where(stockout, 0, np.round(wh_daily * cover_days + rng.poisson(3, len(demand))))
        lead = rng.choice([7, 10, 14, 21, 28, 35], size=len(demand), p=[.15, .2, .25, .2, .12, .08])
        reorder_level = np.ceil(wh_daily * lead * 1.2)
        reorder_qty = np.maximum(np.ceil(wh_daily * 30), 12)
        sup = rng.choice(suppliers, size=len(demand))
        for i, code in enumerate(demand["stock_code"]):
            rows.append((as_of, code, wh_id, int(stock[i]), int(reorder_level[i]), int(reorder_qty[i]), sup[i], int(lead[i])))
    inv = pd.DataFrame(rows, columns=["snapshot_date", "stock_code", "warehouse_id", "stock_qty", "reorder_level",
                                      "reorder_qty", "supplier_id", "supplier_lead_time_days"])
    inv["is_synthetic"] = True
    wh = pd.DataFrame(WAREHOUSES, columns=["warehouse_id", "warehouse_name", "city", "country"])
    wh["is_synthetic"] = True
    k1, k2 = "synthetic/inventory/inventory_snapshot.parquet", "synthetic/inventory/warehouses.parquet"
    inv.to_parquet(store.local_path(k1), index=False)
    wh.to_parquet(store.local_path(k2), index=False)
    store.sync_written(k1), store.sync_written(k2)
    _register(con, run_id, "synthetic_inventory", k1, len(inv))
    log(logger, 20, "synthetic inventory generated", rows=len(inv), as_of=str(as_of))
    return {"rows": len(inv), "as_of": str(as_of)}


def generate_marketing(con, run_id: str, silver_path: str, store: ObjectStore | None = None) -> dict:
    store = store or ObjectStore()
    rng = np.random.default_rng(settings.synthetic_seed + 1)
    d0, d1 = con.execute(f"select min(invoice_date), max(invoice_date) from read_parquet('{silver_path}')").fetchone()
    months = pd.date_range(pd.Timestamp(d0).replace(day=1), pd.Timestamp(d1), freq="MS")
    campaigns, daily = [], []
    regions = ["UK", "UK", "EU", "UK", "International"]
    for m in months:
        for ch, (cpm, ctr, cvr, aov, budget) in CHANNELS.items():
            cid = f"CMP-SYN-{m:%Y%m}-{ch.replace(' ', '')[:6].upper()}"
            seasonal = 1.0 + (0.6 if m.month in (10, 11) else 0.0) + (0.2 if m.month == 9 else 0.0)
            month_budget = budget * seasonal * rng.uniform(0.85, 1.15)
            end = min(m + pd.offsets.MonthEnd(0), pd.Timestamp(d1))
            days = pd.date_range(m, end, freq="D")
            campaigns.append((cid, f"{m:%b %Y} {ch} (synthetic)", ch, "Acquisition" if ch != "Email" else "Retention",
                              m.date(), end.date(), regions[list(CHANNELS).index(ch)], round(month_budget, 2)))
            full_month_days = (m + pd.offsets.MonthEnd(0)).day   # pace on the full month even if data ends early
            for d in days:
                cost = month_budget / full_month_days * rng.lognormal(0, 0.15)
                conv_mult = 1.0
                if ch == "Paid Social" and pd.Timestamp("2011-09-12") <= d < pd.Timestamp("2011-09-19"):
                    cost *= 3.2                                             # injected spend anomaly
                if ch == "Paid Search" and pd.Timestamp("2011-11-01") <= d < pd.Timestamp("2011-11-11"):
                    conv_mult = 0.5                                         # injected conversion drop
                impressions = int(cost / cpm * 1000)
                clicks = int(rng.binomial(impressions, ctr))
                conv = int(rng.binomial(clicks, min(cvr * conv_mult, 1.0)))
                revenue = float(np.sum(rng.gamma(4.0, aov / 4.0, size=conv))) if conv else 0.0
                daily.append((cid, d.date(), round(cost, 2), impressions, clicks, conv, round(revenue, 2)))
    cdf = pd.DataFrame(campaigns, columns=["campaign_id", "campaign_name", "channel", "objective", "start_date",
                                           "end_date", "target_region", "planned_budget"])
    ddf = pd.DataFrame(daily, columns=["campaign_id", "activity_date", "cost", "impressions", "clicks", "conversions",
                                       "attributed_revenue"])
    cdf["is_synthetic"] = True
    ddf["is_synthetic"] = True
    k1, k2 = "synthetic/marketing/campaigns.parquet", "synthetic/marketing/campaign_daily.parquet"
    cdf.to_parquet(store.local_path(k1), index=False)
    ddf.to_parquet(store.local_path(k2), index=False)
    store.sync_written(k1), store.sync_written(k2)
    _register(con, run_id, "synthetic_marketing", k2, len(ddf))
    log(logger, 20, "synthetic marketing generated", campaigns=len(cdf), rows=len(ddf))
    return {"campaigns": len(cdf), "rows": len(ddf), "injected_anomalies": INJECTED_ANOMALIES}
