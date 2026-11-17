from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from dashboard import components as ui


def render() -> None:
    ui.page_header("Data Operations", "Pipeline status, data quality, ingestion, freshness and processing time.")
    if not ui.require_warehouse():
        return
    runs = ui.q("select * from ops.pipeline_runs order by started_at desc")
    tasks = ui.q("select * from ops.task_runs order by started_at desc")
    batches = ui.q("select * from ops.ingestion_batches order by started_at desc")
    dq = ui.q("select * from ops.dq_scores order by checked_at desc")
    fresh = ui.q("select max(invoice_ts) latest_event, max(f.batch_id) batch from gold.fact_sales f").iloc[0]
    last = runs.iloc[0] if len(runs) else None
    ok_runs = runs[runs.status.isin(["success", "partial"])]
    ui.card_row([
        ui.kpi_card("Last run status", ("✅ " if last is not None and last.status in ("success", "partial") else "❌ ")
                    + (last.status if last is not None else "none"), "text"),
        ui.kpi_card("Last run duration (s)", last.duration_s if last is not None else None, "count"),
        ui.kpi_card("Data quality score", dq.overall.iloc[0] if len(dq) else None, "ratio"),
        ui.kpi_card("Failed ingestion batches", (batches.status == "failed").sum(), "count"),
        ui.kpi_card("Run success rate", len(ok_runs) / max(len(runs), 1), "ratio")])
    st.caption(f"Data freshness: latest transaction in gold = **{fresh.latest_event}** (the UCI dataset ends "
               f"2011-12-09; in production the freshness SLA is 24h - see contracts/transactions.yml). "
               f"Last successful load: {ok_runs.finished_at.iloc[0] if len(ok_runs) else 'n/a'}.")
    t1, t2, t3, t4, t5 = st.tabs(["Pipeline runs", "Data quality", "Ingestion batches", "Schema monitoring", "Lineage"])
    with t1:
        st.dataframe(runs, hide_index=True, width="stretch", height=240)
        rid = st.selectbox("Run", runs.run_id, key="ops_run")
        tr = tasks[tasks.run_id == rid].sort_values("started_at")
        fig = px.bar(tr, x="duration_s", y="task", color="status", orientation="h",
                     color_discrete_map={"success": ui.SERIES[2], "failed": ui.SERIES[7]})
        fig.update_layout(yaxis_autorange="reversed", hovermode="closest")
        ui.chart(ui.style(fig, "Task processing duration (s)", 320), "ops_tasks")
        st.dataframe(tr[["task", "attempt", "status", "duration_s", "rows_out", "message"]], hide_index=True,
                     width="stretch")
    with t2:
        if len(dq):
            d = dq.iloc[0]
            ui.card_row([ui.kpi_card(k.title(), d[k], "ratio") for k in
                         ["completeness", "validity", "uniqueness", "consistency", "overall"]])
            st.caption("Dimension score = lowest pass rate among that dimension's rules (warn rules included); "
                       "overall = share of bronze rows that passed all error-level rules.")
        res = ui.q("""select dataset, check_name, check_type, severity, column_name, passed, failed_rows, total_rows,
                             pass_rate, detail from ops.dq_results
                      where run_id = (select run_id from ops.dq_scores order by checked_at desc limit 1)""")
        st.dataframe(res, hide_index=True, width="stretch", height=420,
                     column_config={"pass_rate": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
        qr = ui.q("""select unnest(string_split(dq_reasons, ',')) reason, count(*) rows
                     from bronze.brz_quarantine_transactions group by 1 order by 2 desc""")
        c1, c2 = st.columns(2)
        fig = px.bar(qr, x="rows", y="reason", orientation="h", color_discrete_sequence=[ui.SERIES[1]])
        fig.update_layout(yaxis_autorange="reversed", hovermode="closest")
        c1.plotly_chart(ui.style(fig, "Quarantined rows by rule", 300), width="stretch")
        with c2:
            st.markdown("**Quarantine sample**")
            st.dataframe(ui.q("select * from bronze.brz_quarantine_transactions using sample 200 rows"),
                         hide_index=True, height=280)
    with t3:
        st.dataframe(batches, hide_index=True, width="stretch",
                     column_config={"is_synthetic": st.column_config.CheckboxColumn()})
        st.caption("Batches are de-duplicated by content hash (idempotent re-runs) and CSV drops are filtered "
                   "against the source high-watermark (incremental). Failed batches keep their error message.")
        st.dataframe(ui.q("select * from ops.watermarks"), hide_index=True)
    with t4:
        st.dataframe(ui.q("select dataset, run_id, captured_at, schema_hash, drift from ops.schema_snapshots "
                          "order by captured_at desc"), hide_index=True, width="stretch")
        st.caption("Each run snapshots the silver schema and diffs it with the previous snapshot (added / removed / "
                   "type-changed columns).")
    with t5:
        edges = ui.q("select distinct source_node, target_node, edge_type from ops.lineage_edges")
        if edges.empty:
            ui.empty_state("No lineage recorded", "Run the pipeline.")
        else:
            focus = st.selectbox("Focus on node", ["(all gold)"] + sorted(set(edges.target_node)), key="lin_f")
            if focus == "(all gold)":
                e = edges[edges.target_node.str.startswith("gold.") | (edges.edge_type == "pipeline")]
            else:
                keep, frontier = {focus}, {focus}
                while frontier:
                    up = set(edges[edges.target_node.isin(frontier)].source_node) - keep
                    keep |= up
                    frontier = up
                e = edges[edges.target_node.isin(keep) & edges.source_node.isin(keep)]
            nid = {n: f"n{i}" for i, n in enumerate(sorted(set(e.source_node) | set(e.target_node)))}
            mer = "flowchart LR\n" + "\n".join(f'  {nid[s]}["{s}"] --> {nid[t]}["{t}"]' for s, t in
                                               zip(e.source_node, e.target_node))
            from dashboard.views.business_analyst import _mermaid
            _mermaid(mer, 520)
            st.caption("Lineage from pipeline tasks and the dbt manifest. OpenLineage events are written to "
                       "data/lineage/openlineage_events.jsonl and POSTed to OPENLINEAGE_URL (e.g. Marquez) when set.")
            st.dataframe(e, hide_index=True, width="stretch")
