# Professional workflows (portfolio walkthrough)

Both workflows run on the **same governed gold layer** — the KPI values a Business Analyst
sees in the KPI registry are produced by the same metric layer the Data Analyst queries.

## Data Analyst workflow

| Step | Where | What to show |
|---|---|---|
| 1. Ingest | `./scripts/bp.ps1 pipeline` · Data Operations › Ingestion batches | batch registry, content-hash de-dup, watermark |
| 2. Clean | Data Operations › Data quality | quarantined rows by rule, sample of rejected rows with source row numbers |
| 3. Validate | Data Operations › Data quality / Schema monitoring | expectation results, quality scores, drift snapshots, contract gate |
| 4. Query | Data Analyst › SQL Studio | run *Return rate by country*; try `drop table …` to show the read-only guard; save the query; history |
| 5. Analyse trends | Data Analyst › Exploratory analysis | profile *Daily sales*, Kendall trend test, STL weekly seasonality |
| 6. Build KPIs | Data Analyst › KPI analysis | governed definitions, period vs prior / YoY |
| 7. Statistics | Data Analyst › Statistical analysis | new vs returning AOV test (effect size), chi-square on cancellations by region, anomalies (robust z vs Isolation Forest), cohorts, K-Means |
| 8. Forecast | Predictive Analytics | backtest table where the seasonal baseline beats XGBoost; churn SHAP |
| 9. Dashboards | Executive / Sales / Customers | filters (date, country, category, department) |
| 10. Export | Reports & Exports; download buttons on tables | Excel workbook with an *About* sheet stating sources |

## Business Analyst workflow

| Step | Where | What to show |
|---|---|---|
| 1. Define requirements | BA › Requirements | IDs (BR/FR/NFR/OBJ/AS/CN), MoSCoW, owners, acceptance criteria, linked KPI |
| 2. Establish KPIs | BA › KPI registry | definitions, calculation, target + documented target basis, department, frequency |
| 3. Investigate gaps | BA › Performance & variance; Executive KPI table | actual vs target vs last year, MoM/YoY, department scorecard |
| 4. Root causes | BA › Root-cause investigation | *Why did revenue decline?* for Dec-2010 vs Nov-2010: revenue bridge, contribution by country/category/customer type, hypotheses labelled as such |
| 5. Document process | BA › Process improvement | Mermaid flow with bottleneck steps highlighted, step durations |
| 6. Recommend improvement | BA › Process improvement › Propose | expected impact with basis of estimate |
| 7. Estimate impact | Business Impact Simulator | explicit assumptions; save scenarios and compare |
| 8. Track decisions | BA › Root-cause › *Log a decision*, BA › Decision register | evidence + evidence source carried over automatically |
| 9. Evaluate outcomes | Decision register (Business Manager approves; status → Implemented/Evaluated) | baseline / target / actual → % of target achieved |
| — Alerts | Alerts | acknowledge → investigate → resolve with notes and history |

## Suggested demo storyline (10 minutes)

1. Executive dashboard (trailing 12 months vs the previous 12): net revenue +2.1%, but
   **new-customer acquisition −57%** and the return rate nearly doubled.
2. Alerts: a critical *returns spike* in week 2011-49; evidence shows one 80,995-unit line.
3. Root cause: the single cancellation explains most December returns → hypothesis "keying error".
4. Process improvement + decision DEC-001: add quantity validation at order entry.
5. Simulator: what a 3-point retention gain is worth under stated assumptions.
6. Data Operations: show the quarantine and lineage that make the numbers trustworthy.
