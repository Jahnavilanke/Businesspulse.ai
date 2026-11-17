from __future__ import annotations

import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

from businesspulse.analytics import kpis
from businesspulse.analytics import root_cause as rc
from businesspulse.business_analysis import store
from businesspulse.security import can
from dashboard import components as ui


def _mermaid(code: str, height: int = 320) -> None:
    """Render a Mermaid diagram. Streamlit renders every tab up front, so a diagram in an
    inactive tab sits in a zero-width iframe; wait until it is visible before laying it out."""
    import html as _html
    components.html(f"""
        <pre class="mermaid" style="visibility:hidden">{_html.escape(code)}</pre>
        <script type="module">
          import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.esm.min.mjs';
          mermaid.initialize({{ startOnLoad: false, theme: 'neutral', securityLevel: 'strict' }});
          const el = document.querySelector('.mermaid');
          const draw = async () => {{ await mermaid.run({{ nodes: [el] }}); el.style.visibility = 'visible'; }};
          const ready = () => document.body.clientWidth > 0;
          if (ready()) {{ draw(); }} else {{
            const ro = new ResizeObserver(() => {{ if (ready()) {{ ro.disconnect(); draw(); }} }});
            ro.observe(document.body);
          }}
        </script>""", height=height, scrolling=True)


def _status_chart(df: pd.DataFrame, col: str, title: str) -> None:
    if df.empty:
        return
    c = df[col].value_counts()
    fig = go.Figure(go.Bar(x=c.index, y=c.values, marker_color=ui.SERIES[0]))
    fig.update_layout(hovermode="closest")
    ui.chart(ui.style(fig, title, 220), f"st_{title}")


# ------------------------------------------------------------------ A. requirements
def _requirements(u) -> None:
    df = store.list_records("requirements")
    c1, c2, c3 = st.columns(3)
    ftype = c1.multiselect("Type", store.REQ_TYPES, key="rq_t")
    fstat = c2.multiselect("Status", store.STATUSES["requirements"], key="rq_s")
    fprio = c3.multiselect("Priority (MoSCoW)", ["Must", "Should", "Could", "Won't"], key="rq_p")
    v = df
    if ftype:
        v = v[v.req_type.isin(ftype)]
    if fstat:
        v = v[v.status.isin(fstat)]
    if fprio:
        v = v[v.priority.isin(fprio)]
    ui.searchable_table(v.drop(columns=["created_at"]), "rq_tbl", 320)
    ui.download_buttons(v, "business_requirements", "rq_dl")
    _status_chart(df, "status", "Requirements by status")
    if not can(u.role, "edit_requirements"):
        st.info("Read-only: your role cannot edit requirements.")
        return
    with st.expander("➕ Add / ✏️ edit requirement", expanded=False):
        ids = ["(new)"] + df.req_id.tolist()
        sel = st.selectbox("Requirement", ids, key="rq_sel")
        cur = df[df.req_id == sel].iloc[0].to_dict() if sel != "(new)" else {}
        with st.form("rq_form", clear_on_submit=False):
            a, b, c = st.columns(3)
            rtype = a.selectbox("Type", store.REQ_TYPES, index=store.REQ_TYPES.index(cur.get("req_type", "Business")))
            prio = b.selectbox("Priority", ["Must", "Should", "Could", "Won't"],
                               index=["Must", "Should", "Could", "Won't"].index(cur.get("priority", "Should")))
            status = c.selectbox("Status", store.STATUSES["requirements"],
                                 index=store.STATUSES["requirements"].index(cur.get("status", "Draft")))
            title = st.text_input("Title", cur.get("title", ""))
            desc = st.text_area("Description", cur.get("description", "") or "")
            a, b, c = st.columns(3)
            stake = a.text_input("Stakeholder", cur.get("stakeholder", "") or "")
            owner = b.text_input("Owner", cur.get("owner", "") or "")
            kpi_opts = [""] + list(kpis.METRICS)
            kpi = c.selectbox("Linked KPI", kpi_opts, index=kpi_opts.index(cur.get("linked_kpi") or ""))
            ac = st.text_area("Acceptance criteria", cur.get("acceptance_criteria", "") or "")
            ok = st.form_submit_button("Save requirement", type="primary")
        if ok:
            if not title.strip():
                st.error("Title is required.")
            else:
                rid = sel if sel != "(new)" else store.next_id("requirements", store.REQ_PREFIX[rtype])
                store.upsert("requirements", {"req_id": rid, "req_type": rtype, "title": title, "description": desc,
                                              "stakeholder": stake, "priority": prio, "owner": owner, "status": status,
                                              "acceptance_criteria": ac, "linked_kpi": kpi or None,
                                              "is_demo": int(cur.get("is_demo", 0) or 0)}, u.username, u.role)
                st.success(f"Saved {rid}")
                st.rerun()


# ------------------------------------------------------------------ B. KPI registry
def _kpi_registry(u) -> None:
    f = ui.filters()
    reg = store.list_records("kpi_registry")
    vals = ui.kpi_values(f["start"], f["end"])
    months = max(1, round((pd.Timestamp(f["end"]) - pd.Timestamp(f["start"])).days / 30.4))
    reg["actual"] = reg.kpi_id.map(vals)
    volume = {"net_revenue", "orders", "new_customers", "gross_sales", "units_sold"}
    reg["period_target"] = [t * months if k in volume and t == t and t is not None else t
                            for k, t in zip(reg.kpi_id, reg.target_value)]
    reg["variance"] = reg.actual - reg.period_target
    reg["variance_pct"] = reg.variance / reg.period_target.abs()
    if f.get("department") not in (None, "All"):
        reg = reg[reg.department == f["department"]]
    st.caption(f"Actuals for {f['start']} → {f['end']} ({months} month(s)); volume targets are monthly × months.")
    st.dataframe(reg[["kpi_id", "name", "business_definition", "calculation", "unit", "target_value", "period_target",
                      "actual", "variance", "variance_pct", "department", "frequency", "data_basis", "target_basis"]],
                 hide_index=True, width="stretch", height=430,
                 column_config={"variance_pct": st.column_config.NumberColumn(format="percent"),
                                "actual": st.column_config.NumberColumn(format="%.4g"),
                                "period_target": st.column_config.NumberColumn(format="%.4g")})
    ui.download_buttons(reg, "kpi_registry", "kpi_reg_dl")
    if can(u.role, "edit_kpi_targets"):
        with st.expander("🎯 Configure business targets"):
            with st.form("kpi_tgt"):
                k = st.selectbox("KPI", reg.kpi_id, format_func=lambda x: reg.set_index("kpi_id").loc[x, "name"])
                row = reg.set_index("kpi_id").loc[k]
                t = st.number_input("Monthly target value", value=float(row.target_value) if row.target_value == row.target_value
                                    and row.target_value is not None else 0.0, format="%.4f")
                basis = st.text_input("How was this target set? (documented)", row.target_basis or "")
                dept = st.text_input("Responsible department", row.department or "")
                freq = st.selectbox("Reporting frequency", ["Daily", "Weekly", "Monthly", "Quarterly"],
                                    index=["Daily", "Weekly", "Monthly", "Quarterly"].index(row.frequency or "Monthly"))
                if st.form_submit_button("Save target", type="primary"):
                    rec = row.to_dict()
                    rec.update(kpi_id=k, target_value=t, target_basis=basis, department=dept, frequency=freq,
                               updated_by=u.username)
                    store.upsert("kpi_registry", rec, u.username, u.role)
                    st.success("Target saved")
                    st.rerun()


# ------------------------------------------------------------------ C. performance analysis
def _performance() -> None:
    f = ui.filters()
    m = ui.q("select * from gold.mart_kpi_monthly order by month")
    m["month"] = pd.to_datetime(m["month"])
    reg = store.list_records("kpi_registry").set_index("kpi_id")
    metric = st.selectbox("Metric", ["net_revenue", "orders", "avg_order_value", "active_customers", "new_customers",
                                     "retention_rate", "return_rate"],
                          format_func=lambda k: kpis.METRICS[k].name, key="perf_metric")
    s = m.set_index("month")[metric]
    df = pd.DataFrame({"actual": s, "prev_month": s.shift(1), "last_year": s.shift(12)})
    df["mom_pct"] = df.actual / df.prev_month - 1
    df["yoy_pct"] = df.actual / df.last_year - 1
    tgt = reg.loc[metric, "target_value"] if metric in reg.index else None
    if tgt is not None and tgt == tgt:
        df["target"] = tgt
        df["vs_target_pct"] = df.actual / tgt - 1
    df["partial_month"] = m.set_index("month").is_partial_month
    fig = go.Figure()
    fig.add_bar(x=df.index, y=df.actual, name="Actual", marker_color=ui.SERIES[0])
    fig.add_scatter(x=df.index, y=df.last_year, name="Same month last year", mode="lines", line_color=ui.SERIES[1])
    if "target" in df:
        fig.add_scatter(x=df.index, y=df.target, name="Target (budget)", mode="lines", line=dict(color=ui.INK, dash="dot"))
    ui.chart(ui.style(fig, f"{kpis.METRICS[metric].name}: actual vs last year vs target", 340), "perf_main")
    st.dataframe(df.reset_index(), hide_index=True, width="stretch",
                 column_config={c: st.column_config.NumberColumn(format="percent") for c in
                                ["mom_pct", "yoy_pct", "vs_target_pct"]})
    st.caption("Budget vs actual uses the KPI registry target as the monthly budget (a documented planning "
               "assumption) - the source contains no budget data.")
    st.markdown("#### Product, regional & department performance")
    cur = (f["start"], f["end"])
    prev = kpis.shift_period(*cur, "yoy")
    dim = st.radio("Breakdown", ["category", "country", "customer_segment", "product"], horizontal=True, key="perf_dim")
    t = rc.contribution(dim, cur, prev, top=20)
    fig = go.Figure(go.Bar(x=t.change, y=t.member, orientation="h",
                           marker_color=[ui.DIVERGING[4] if v >= 0 else ui.DIVERGING[0] for v in t.change]))
    fig.update_layout(yaxis_autorange="reversed", hovermode="closest", xaxis_tickprefix="£")
    ui.chart(ui.style(fig, f"Net revenue variance by {dim} vs same period last year ({prev[0]} → {prev[1]})",
                      max(300, 22 * len(t))), "perf_dim_chart")
    st.markdown("**Department scorecard** (KPIs grouped by responsible department)")
    vals = ui.kpi_values(*cur)
    sc = store.list_records("kpi_registry")
    sc["actual"] = sc.kpi_id.map(vals)
    sc = sc.dropna(subset=["actual", "target_value"])
    months = max(1, round((pd.Timestamp(cur[1]) - pd.Timestamp(cur[0])).days / 30.4))
    sc["target"] = [t * months if k in {"net_revenue", "orders", "new_customers"} else t
                    for k, t in zip(sc.kpi_id, sc.target_value)]
    sc["on_target"] = [(a >= t) if d == "higher_is_better" else (a <= t)
                       for a, t, d in zip(sc.actual, sc.target, sc.direction)]
    dept = sc.groupby("department").agg(kpis=("kpi_id", "count"), on_target=("on_target", "sum")).reset_index()
    dept["attainment"] = dept.on_target / dept.kpis
    st.dataframe(dept, hide_index=True, width="stretch",
                 column_config={"attainment": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})


# ------------------------------------------------------------------ D. root cause
def _root_cause(u) -> None:
    q = st.selectbox("Question to investigate", list(rc.INVESTIGATIONS), key="rc_q")
    f = ui.filters()
    d0, d1 = kpis.data_range()
    c1, c2, c3 = st.columns(3)
    default_cur = (date(2011, 11, 1), date(2011, 11, 30)) if d1 >= date(2011, 11, 30) else (f["start"], f["end"])
    cur = c1.date_input("Investigation period", default_cur, min_value=d0, max_value=d1, key="rc_cur")
    mode = c2.selectbox("Compare with", ["Previous period", "Same period last year", "Custom"], key="rc_mode")
    if not (isinstance(cur, tuple) and len(cur) == 2):
        st.info("Select a start and end date.")
        return
    if mode == "Custom":
        prev = c3.date_input("Comparison period", kpis.shift_period(*cur, "prior"), key="rc_prev")
        if not (isinstance(prev, tuple) and len(prev) == 2):
            return
    else:
        prev = kpis.shift_period(*cur, "prior" if mode == "Previous period" else "yoy")
        c3.text_input("Comparison period", f"{prev[0]} → {prev[1]}", disabled=True)
    with st.spinner("Computing evidence…"):
        inv = rc.INVESTIGATIONS[q](tuple(cur), tuple(prev))
    if inv.data_basis == "synthetic":
        ui.synthetic_banner()
    st.markdown("#### Findings")
    for fd in inv.findings:
        icon = {"observation": "📌", "hypothesis": "🧪", "data_note": "ℹ️"}[fd.kind]
        label = {"observation": "Observation", "hypothesis": "Hypothesis to validate", "data_note": "Data note"}[fd.kind]
        st.markdown(f"{icon} **{label}:** {fd.observation}")
    for c in inv.caveats:
        st.warning(c, icon="⚖️")
    if "revenue_bridge" in inv.tables and len(inv.tables["revenue_bridge"]):
        b = inv.tables["revenue_bridge"]
        fig = go.Figure(go.Waterfall(x=b.component, y=b.value, measure=["absolute", "relative", "relative", "relative", "total"],
                                     connector={"line": {"color": ui.GRID}},
                                     increasing={"marker": {"color": ui.DIVERGING[4]}},
                                     decreasing={"marker": {"color": ui.DIVERGING[0]}},
                                     totals={"marker": {"color": ui.SERIES[0]}}))
        fig.update_layout(hovermode="closest", yaxis_tickprefix="£", yaxis_tickformat="~s")
        ui.chart(ui.style(fig, "Revenue bridge: comparison → current", 340), "rc_bridge")
    for name, t in inv.tables.items():
        if name == "revenue_bridge" or t is None or t.empty:
            continue
        with st.expander(f"Evidence: {name.replace('_', ' ')} ({len(t)} rows)"):
            st.dataframe(t, hide_index=True, width="stretch")
    if can(u.role, "edit_decisions"):
        with st.expander("➡️ Log a decision from this investigation"):
            with st.form("rc_dec"):
                problem = st.text_input("Problem", q)
                action = st.text_area("Proposed action")
                owner = st.text_input("Business owner")
                metric = st.selectbox("Success metric", list(kpis.METRICS))
                if st.form_submit_button("Create decision", type="primary"):
                    did = store.next_id("decisions", "DEC")
                    ev = " | ".join(fd.observation for fd in inv.findings[:4])
                    store.upsert("decisions", {"decision_id": did, "problem": problem, "evidence": ev,
                                               "evidence_source": f"root-cause:{q} {cur[0]}..{cur[1]} vs {prev[0]}..{prev[1]}",
                                               "proposed_action": action, "owner": owner, "priority": "Medium",
                                               "success_metric": metric, "status": "Proposed", "is_demo": 0},
                                 u.username, u.role)
                    st.success(f"Created {did} in the decision register.")


# ------------------------------------------------------------------ E. process analysis
def _process(u) -> None:
    procs = store.list_records("processes")
    imps = store.list_records("improvements")
    if procs.empty:
        ui.empty_state("No processes documented", "Add a process below.")
    else:
        pid = st.selectbox("Process", procs.process_id, format_func=lambda p: f"{p} - {procs.set_index('process_id').loc[p, 'name']}",
                           key="pr_sel")
        p = procs.set_index("process_id").loc[pid]
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown(f"**{p['name']}** · {p.department} · owner {p.owner}" + (" · *demo record*" if p.is_demo else ""))
            if p.mermaid:
                _mermaid(p.mermaid)
        with c2:
            st.markdown(f"**Current state:** {p.current_state}")
            st.markdown(f"**Bottlenecks:** {p.bottlenecks}")
            st.markdown(f"**Identified issues:** {p.issues}")
        steps = pd.DataFrame(json.loads(p.steps_json or "[]"))
        if len(steps):
            tot = steps.duration_hrs.sum()
            bot = steps[steps.bottleneck].duration_hrs.sum()
            st.markdown(f"**Process steps** - total {tot:.0f} h per cycle, of which {bot:.0f} h ({bot / tot:.0%}) in "
                        "bottleneck steps (documented durations).")
            st.dataframe(steps, hide_index=True, width="stretch")
        st.markdown("**Improvement tracker**")
        pi = imps[imps.process_id == pid]
        st.dataframe(pi, hide_index=True, width="stretch")
    if not can(u.role, "edit_processes"):
        return
    with st.expander("➕ Document a process"):
        with st.form("pr_form"):
            name = st.text_input("Process name")
            a, b = st.columns(2)
            dept = a.text_input("Responsible department")
            owner = b.text_input("Owner")
            cs = st.text_area("Current process description")
            steps_txt = st.text_area("Steps (one per line: step | owner | hours | bottleneck yes/no)",
                                     "Receive request | Sales | 1 | no\nManual approval | Finance | 24 | yes")
            bott = st.text_input("Bottlenecks")
            iss = st.text_area("Identified issues")
            if st.form_submit_button("Save process", type="primary") and name:
                steps = []
                for line in steps_txt.splitlines():
                    parts = [x.strip() for x in line.split("|")]
                    if len(parts) >= 3:
                        steps.append({"step": parts[0], "owner": parts[1], "duration_hrs": float(parts[2] or 0),
                                      "bottleneck": len(parts) > 3 and parts[3].lower().startswith("y")})
                mer = "flowchart LR\n" + "\n".join(
                    f"  S{i}[\"{s['step']} ({s['owner']}, {s['duration_hrs']:g}h)\"]" + (f" --> S{i + 1}" if i < len(steps) - 1 else "")
                    for i, s in enumerate(steps)) + "".join(f"\n  style S{i} fill:#fde2e1" for i, s in enumerate(steps)
                                                             if s["bottleneck"])
                pid = store.next_id("processes", "PRC")
                store.upsert("processes", {"process_id": pid, "name": name, "department": dept, "owner": owner,
                                           "current_state": cs, "steps_json": json.dumps(steps), "bottlenecks": bott,
                                           "issues": iss, "mermaid": mer, "is_demo": 0}, u.username, u.role)
                st.success(f"Saved {pid}")
                st.rerun()
    if not procs.empty:
        with st.expander("➕ Propose an improvement"):
            with st.form("imp_form"):
                pid2 = st.selectbox("Process", procs.process_id)
                title = st.text_input("Improvement title")
                prop = st.text_area("Proposed improvement")
                imp = st.text_input("Expected business impact (state basis of estimate)")
                a, b, c = st.columns(3)
                metric = a.text_input("Impact metric")
                owner = b.text_input("Owner ")
                status = c.selectbox("Status", store.STATUSES["improvements"])
                if st.form_submit_button("Save improvement", type="primary") and title:
                    iid = store.next_id("improvements", "IMP")
                    store.upsert("improvements", {"improvement_id": iid, "process_id": pid2, "title": title,
                                                  "proposal": prop, "expected_impact": imp, "impact_metric": metric,
                                                  "owner": owner, "status": status, "is_demo": 0}, u.username, u.role)
                    st.success(f"Saved {iid}")
                    st.rerun()


# ------------------------------------------------------------------ F. decisions
def _decisions(u) -> None:
    d = store.list_records("decisions")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Decisions", len(d))
    c2.metric("Open (Proposed/Approved/In Progress)", int(d.status.isin(["Proposed", "Approved", "In Progress"]).sum()))
    c3.metric("Implemented / Evaluated", int(d.status.isin(["Implemented", "Evaluated"]).sum()))
    c4.metric("Estimated cost (open)", f"£{d.loc[d.status.isin(['Proposed', 'Approved', 'In Progress']), 'estimated_cost'].sum():,.0f}")
    ui.searchable_table(d, "dec_tbl", 300)
    ui.download_buttons(d, "decision_register", "dec_dl")
    if not can(u.role, "edit_decisions"):
        st.info("Read-only for your role.")
        return
    with st.expander("➕ Add / ✏️ update decision", expanded=False):
        sel = st.selectbox("Decision", ["(new)"] + d.decision_id.tolist(), key="dec_sel")
        cur = d[d.decision_id == sel].iloc[0].to_dict() if sel != "(new)" else {}
        statuses = store.STATUSES["decisions"]
        allowed = statuses if can(u.role, "approve_decisions") else [s for s in statuses if s not in {"Approved"}]
        if cur.get("status") and cur["status"] not in allowed:
            allowed = [cur["status"]] + allowed
        with st.form("dec_form"):
            problem = st.text_input("Identified business problem", cur.get("problem", ""))
            evidence = st.text_area("Supporting evidence", cur.get("evidence", "") or "")
            src = st.text_input("Evidence source (alert id, query, investigation)", cur.get("evidence_source", "") or "")
            action = st.text_area("Proposed action", cur.get("proposed_action", "") or "")
            outcome = st.text_input("Expected outcome", cur.get("expected_outcome", "") or "")
            a, b, c = st.columns(3)
            owner = a.text_input("Business owner", cur.get("owner", "") or "")
            prio = b.selectbox("Priority", ["High", "Medium", "Low"],
                               index=["High", "Medium", "Low"].index(cur.get("priority") or "Medium"))
            cost = c.number_input("Estimated cost (£)", value=float(cur.get("estimated_cost") or 0.0), step=500.0)
            a, b, c = st.columns(3)
            metric = a.selectbox("Success metric", list(kpis.METRICS),
                                 index=list(kpis.METRICS).index(cur.get("success_metric") or "net_revenue"))
            base = b.number_input("Baseline value", value=float(cur.get("baseline_value") or 0.0), format="%.4f")
            tgt = c.number_input("Target value", value=float(cur.get("target_value") or 0.0), format="%.4f")
            status = st.selectbox("Decision status", allowed, index=allowed.index(cur.get("status", "Proposed")))
            a, b = st.columns(2)
            actual_outcome = a.text_area("Actual outcome (after implementation)", cur.get("actual_outcome", "") or "")
            actual_val = b.number_input("Actual value of success metric", value=float(cur.get("actual_value") or 0.0),
                                        format="%.4f")
            st.caption("Approval requires the Business Manager or Administrator role. Record actual outcomes only "
                       "from measured results.")
            if st.form_submit_button("Save decision", type="primary") and problem:
                did = sel if sel != "(new)" else store.next_id("decisions", "DEC")
                store.upsert("decisions", {"decision_id": did, "problem": problem, "evidence": evidence,
                                           "evidence_source": src, "proposed_action": action, "expected_outcome": outcome,
                                           "owner": owner, "priority": prio, "estimated_cost": cost,
                                           "success_metric": metric, "baseline_value": base or None,
                                           "target_value": tgt or None, "status": status,
                                           "actual_outcome": actual_outcome or None, "actual_value": actual_val or None,
                                           "is_demo": int(cur.get("is_demo", 0) or 0)}, u.username, u.role)
                st.success(f"Saved {did}")
                st.rerun()
    ev = d[d.status.isin(["Implemented", "Evaluated"]) & d.actual_value.notna() & d.target_value.notna()]
    if len(ev):
        st.markdown("**Outcome evaluation**")
        ev = ev.assign(achieved_pct=(ev.actual_value - ev.baseline_value) / (ev.target_value - ev.baseline_value))
        st.dataframe(ev[["decision_id", "success_metric", "baseline_value", "target_value", "actual_value", "achieved_pct"]],
                     hide_index=True, width="stretch")


def render() -> None:
    ui.page_header("Business Analyst Workspace", "Requirements → KPIs → performance gaps → root causes → process "
                   "improvements → decisions → outcomes.", "actual")
    if not ui.require_warehouse():
        return
    u = ui.user()
    tabs = st.tabs(["📋 Requirements", "🎯 KPI registry", "📈 Performance & variance", "🔎 Root-cause investigation",
                    "🔄 Process improvement", "🗂️ Decision register"])
    with tabs[0]:
        _requirements(u)
    with tabs[1]:
        _kpi_registry(u)
    with tabs[2]:
        _performance()
    with tabs[3]:
        _root_cause(u)
    with tabs[4]:
        _process(u)
    with tabs[5]:
        _decisions(u)
