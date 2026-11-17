# Models & evaluation

All metrics below come from the latest pipeline run (`ops.model_runs`, also logged to
MLflow under `data/mlruns`). Every evaluation uses data **later in time** than the training
data. Where a baseline wins, the baseline is used.

## Revenue forecast — weekly net revenue, 12-week horizon

Validation: holdout of the last 12 complete weeks (2011-09-12 → 2011-11-28); partial first
and last weeks excluded.

| Model | WAPE | MAPE | MAE (£) |
|---|---|---|---|
| **Seasonal naive (same week last year)** | **0.148** | 0.169 | 41,423 |
| XGBoost, recursive lag features | 0.186 | 0.182 | 52,021 |
| 4-week moving average | 0.418 | 0.396 | 116,698 |
| Holt-Winters (additive, 52-week season) | attempted; not estimable with ~2 seasons of history — skipped automatically | | |

Selected: seasonal naive. Interval = ±1.96 × backtest residual std (approximate).
The holdout is the Q4 peak; strong annual seasonality explains why last-year-same-week wins.

## Demand forecast — weekly units, top 50 products, 4-week horizon

Validation: last 8 weeks across 50 products.

| Model | WAPE | MAE (units) |
|---|---|---|
| **4-week moving average** | **0.528** | 266.8 |
| Global XGBoost (Tweedie) | 0.590 | 298.1 |
| Seasonal naive | 0.925 | 466.8 |

Selected: 4-week moving average. SKU-level wholesale demand is lumpy; WAPE above 0.5 means
forecasts should inform, not automate, replenishment.

## Churn — no purchase in the next 90 days

Validation: out-of-time. Train on features before 2011-06-12 (label = next 90 days), test on
features before 2011-09-10 (label 2011-09-10 → 2011-12-09). Test churn base rate 56.5%.

| Model | ROC AUC | PR AUC | Brier |
|---|---|---|---|
| **Logistic regression (balanced)** | **0.789** | **0.810** | **0.185** |
| XGBoost | 0.786 | 0.801 | 0.201 |

Selected: logistic regression. Explanations: global mean |SHAP| from the XGBoost model; per
customer top-3 drivers from the selected model (coefficient × standardized feature, i.e.
linear SHAP). Features use only data before the scoring date (no leakage).

## Customer value (CLV) — net revenue in the next 180 days

Validation: train cutoff 2010-12-14, test cutoff 2011-06-12.

| Model | MAE (£) | Spearman | Top-decile revenue capture |
|---|---|---|---|
| Persistence (last 180 days) | **549** | 0.567 | 0.616 |
| XGBoost-Tweedie | 580 | **0.589** | **0.633** |

XGBoost ranks customers slightly better but has higher absolute error — use it for
prioritisation, not for financial value estimates.

## Limitations

* 2 years of history from one retailer, 2009–2011; behaviour may not transfer.
* Models describe historical associations; SHAP is not causal.
* No hyper-parameter search (deliberately simple, reproducible defaults, seed 42).
