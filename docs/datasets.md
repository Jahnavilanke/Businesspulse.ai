# Datasets, provenance and limitations

Machine-readable catalog: [`config/sources.yml`](../config/sources.yml). Acquisition
manifest (sha256, size, validation, license) is written to
`data/landing/uci_online_retail_ii/_manifest.json` on every run.

## 1. UCI Online Retail II — primary, REAL

| | |
|---|---|
| Source | UCI Machine Learning Repository, https://archive.ics.uci.edu/dataset/502/online+retail+ii (DOI 10.24432/C5CG6D) |
| Creator / license | Daqing Chen · CC BY 4.0 |
| Coverage | 2009-12-01 → 2011-12-09, two sheets (`Year 2009-2010`, `Year 2010-2011`) |
| Size | 1,067,371 lines; 5,942 customer ids; 5,305 stock codes; 43 country labels |
| Schema | Invoice, StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country |
| Used for | sales, customers, products, geography, returns (cancellations), demand, forecasting, churn, CLV |

**Findings from profiling (drive the quality rules):**
* The two sheets overlap on 2010-12-01…09 — 22,202 overlapping lines; together with
  in-sheet exact duplicates, 34,335 duplicate lines are quarantined (first copy kept).
* 243,007 lines (22.8%) have no Customer ID → kept as *guest* revenue, excluded from customer metrics.
* 19,494 cancellation lines (`C` invoices); 3,457 negative-quantity lines on normal invoices
  are warehouse write-offs ("damaged", "check", "?") and are quarantined, not treated as sales.
* Non-product codes (POST, DOT, M, D, BANK CHARGES, AMAZONFEE, ADJUST, B…) are classified by
  `dbt/seeds/non_product_codes.csv` and excluded from product KPIs.
* A handful of very large keying errors (e.g. 80,995 units) are later cancelled; they create
  the large Jan-2011 and Dec-2011 return spikes that the alerting module flags.

**Derived (not source) attributes — documented, never presented as source facts:**
registration date = first purchase date; home country = most frequent invoice country;
RFM segment and loyalty tier = rules on purchase history; product category = keyword rules
(`dbt/seeds/category_rules.csv`, ~20% unclassified); sales channel = "Online" (single channel).

**Not available → shown as unavailable:** product cost / gross profit, budgets, stores,
real loyalty programme, marketing attribution, stock levels.

## 2. ECB FX reference rates via Frankfurter — optional, REAL

Monthly averages of daily ECB rates (GBP→USD/EUR) for the transaction period; used only for
currency reporting. Offline → stage reports `unavailable`, platform continues in GBP.

## 3. Synthetic inventory snapshot — DEMONSTRATION ONLY

One as-of snapshot (2011-12-09) for the real product catalogue × 3 synthetic warehouses
(14,172 rows). Stock = real trailing 90-day demand × random days-of-cover (gamma), ~6%
random stock-outs, random supplier lead times. **No inventory history is invented**; the
snapshot is never joined to past transactions as if it explained them.

## 4. Synthetic marketing campaigns — DEMONSTRATION ONLY

125 monthly campaigns across 5 channels (3,695 daily rows) generated from funnel
assumptions (CPM, CTR, CVR, AOV) calibrated to plausible retail ROAS. Attributed revenue is
generated, **not** linked to real orders. Two documented anomalies are injected so alert
detection can be demonstrated: Paid Social spend ×3.2 from 2011-09-12 (7 days) and Paid
Search conversion rate halved from 2011-11-01 (10 days).

## Key compatibility

Only legitimate shared keys are used: `stock_code` (real catalogue) and calendar dates.
Customer, country and invoice keys of real transactions are never attached to synthetic rows.
