"""What-if business scenario simulator.

Baseline = ACTUAL trailing-12-month results from the warehouse. Every lever is applied
through an explicit, user-visible assumption (elasticity, ROAS, margin...). Outputs are
MODELLED ESTIMATES, never actual outcomes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from ..analytics import kpis
from ..db import query_df


@dataclass
class Assumptions:
    price_change_pct: float = 0.0          # +5 = prices up 5%
    price_elasticity: float = -1.2         # % volume change per 1% price change (assumption, not estimated)
    discount_pct: float = 0.0              # extra across-the-board discount
    discount_volume_lift: float = 1.5      # % volume lift per 1% discount (assumption)
    volume_change_pct: float = 0.0         # exogenous demand change
    marketing_budget_change: float = 0.0   # GBP per year added (+) or removed (-)
    incremental_roas: float = 2.0          # revenue per extra £1 of marketing (assumption; synthetic benchmark)
    retention_change_pp: float = 0.0       # change in month-over-month retention, percentage points
    revenue_per_retained_customer: float | None = None  # default: actual average
    gross_margin_pct: float | None = None  # NO actual cost data: user must supply to see profit
    reorder_qty_change_pct: float = 0.0    # change in reorder quantities (synthetic inventory)


def baseline() -> dict:
    d0, d1 = kpis.data_range()
    end = pd.Timestamp(d1)
    start = (end - pd.DateOffset(years=1) + pd.Timedelta(days=1)).date()
    k = kpis.compute_kpis(start, end.date())
    m = query_df("""select avg(active_customers) ac, avg(retention_rate) rr from gold.mart_kpi_monthly
                    where month >= date_trunc('month', ?::date) and not is_partial_month""", [start]).iloc[0]
    out = {"period": f"{start} to {end.date()}", "net_revenue": k["net_revenue"], "gross_sales": k["gross_sales"],
           "returns_value": k["returns_value"], "orders": k["orders"], "units_sold": k["units_sold"],
           "aov": k["avg_order_value"], "avg_active_customers": float(m.ac), "retention_rate": float(m.rr),
           "revenue_per_active_customer_month": k["net_revenue"] / 12 / float(m.ac)}
    if kpis.synthetic_available():
        inv = query_df("select sum(stock_qty) stock, sum(reorder_qty) reorder, sum(units_last_365d_actual) units "
                       "from gold.fact_inventory").iloc[0]
        mk = query_df("select sum(cost) cost, sum(attributed_revenue) rev from gold.fact_marketing "
                      "where activity_date between ? and ?", [start, end.date()]).iloc[0]
        out.update(stock_units=float(inv.stock), reorder_units=float(inv["reorder"]), marketing_cost=float(mk.cost),
                   marketing_revenue=float(mk.rev))
    return out


def simulate(a: Assumptions, base: dict | None = None) -> dict:
    b = base or baseline()
    price_factor = 1 + a.price_change_pct / 100
    vol_factor = (1 + a.price_elasticity * a.price_change_pct / 100) * (1 + a.discount_volume_lift * a.discount_pct / 100) \
        * (1 + a.volume_change_pct / 100)
    vol_factor = max(vol_factor, 0)
    disc_factor = 1 - a.discount_pct / 100
    gross = b["gross_sales"] * price_factor * disc_factor * vol_factor
    return_rate = b["returns_value"] / b["gross_sales"]
    returns = gross * return_rate
    rev_core = gross - returns
    rpc = a.revenue_per_retained_customer or b["revenue_per_active_customer_month"]
    retained_extra = b["avg_active_customers"] * a.retention_change_pp / 100 * 12   # customer-months gained
    rev_retention = retained_extra * rpc
    rev_marketing = a.marketing_budget_change * a.incremental_roas
    net = rev_core + rev_retention + rev_marketing
    res = {"net_revenue": net, "gross_sales": gross, "returns_value": returns, "units_sold": b["units_sold"] * vol_factor,
           "orders": b["orders"] * vol_factor, "revenue_from_pricing_volume": rev_core - b["net_revenue"],
           "revenue_from_retention": rev_retention, "revenue_from_marketing": rev_marketing,
           "change_vs_baseline": net - b["net_revenue"], "change_pct": (net - b["net_revenue"]) / b["net_revenue"]}
    if a.gross_margin_pct is not None:
        # Margin applies to list price; price increases add straight to margin, discounts remove from it.
        unit_cost_ratio = (1 - a.gross_margin_pct / 100)
        cogs = b["gross_sales"] * unit_cost_ratio * vol_factor
        res["gross_profit"] = rev_core + rev_retention + rev_marketing - cogs - a.marketing_budget_change
        res["baseline_gross_profit"] = b["net_revenue"] - b["gross_sales"] * unit_cost_ratio
    if "marketing_cost" in b:
        cost = b["marketing_cost"] + a.marketing_budget_change
        res["marketing_cost"] = cost
        res["marketing_roas"] = (b["marketing_revenue"] + rev_marketing) / cost if cost > 0 else None
        res["baseline_marketing_roas"] = b["marketing_revenue"] / b["marketing_cost"]
    if "stock_units" in b:
        daily = b["units_sold"] * vol_factor / 365
        reorder = b["reorder_units"] * (1 + a.reorder_qty_change_pct / 100)
        res["stock_units_after_one_cycle"] = b["stock_units"] + reorder - daily * 30
        res["days_of_cover"] = res["stock_units_after_one_cycle"] / daily if daily else None
        res["baseline_days_of_cover"] = (b["stock_units"] + b["reorder_units"] - b["units_sold"] / 365 * 30) / (
            b["units_sold"] / 365)
    res["retention_rate"] = b["retention_rate"] + a.retention_change_pp / 100
    return res


def explain(a: Assumptions) -> list[str]:
    notes = ["Baseline is actual trailing-12-month performance; results are modelled estimates, not forecasts of fact.",
             f"Price elasticity {a.price_elasticity} is an assumption (no price experiments exist in the data)."]
    if a.discount_pct:
        notes.append(f"Each 1% discount assumed to lift volume {a.discount_volume_lift}%.")
    if a.marketing_budget_change:
        notes.append(f"Each extra £1 of marketing assumed to return £{a.incremental_roas} revenue "
                     "(benchmark from SYNTHETIC campaign data / user input).")
    if a.retention_change_pp:
        notes.append("Retention uplift converts to revenue at the actual average monthly revenue per active customer.")
    if a.gross_margin_pct is None:
        notes.append("Gross profit not shown: the source has no product cost. Enter an assumed margin to model it.")
    else:
        notes.append(f"Gross margin {a.gross_margin_pct}% is a user-entered assumption, not actual cost data.")
    return notes


def to_dict(a: Assumptions) -> dict:
    return asdict(a)
