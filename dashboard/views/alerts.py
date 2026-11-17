from __future__ import annotations

import json

import streamlit as st

from businesspulse.alerts.detector import OWNERS, VALID_TRANSITIONS, run_detection, transition
from businesspulse.business_analysis.store import audit
from businesspulse.db import app_query
from businesspulse.security import can
from dashboard import components as ui

SEV = {"critical": "🔴 critical", "high": "🟠 high", "medium": "🟡 medium", "low": "⚪ low"}


def render() -> None:
    ui.page_header("Business Alerts", "Automated statistical monitoring with an acknowledge → investigate → "
                   "resolve workflow.", "mixed")
    if not ui.require_warehouse():
        return
    u = ui.user()
    a = app_query("select * from alerts order by detection_date desc")
    if a.empty:
        ui.empty_state("No alerts yet", "Run the pipeline (detect_alerts stage) or click below.")
    if can(u.role, "manage_alerts") and st.button("🔁 Re-run detection now"):
        with st.spinner("Scanning metrics…"):
            res = run_detection()
        audit(u.username, u.role, "run_alert_detection", "alerts", detail=json.dumps(res, default=str))
        st.success(f"{res['new']} new, {res['updated']} updated")
        st.rerun()
    if a.empty:
        return
    c1, c2, c3, c4 = st.columns(4)
    fs = c1.multiselect("Status", ["Open", "Acknowledged", "Investigating", "Resolved", "Dismissed"],
                        default=["Open", "Acknowledged", "Investigating"], key="al_s")
    fr = c2.multiselect("Rule", sorted(a.rule.unique()), key="al_r")
    fb = c3.multiselect("Data basis", ["actual", "synthetic"], default=["actual", "synthetic"], key="al_b")
    fv = c4.multiselect("Severity", list(SEV), key="al_v")
    v = a[a.status.isin(fs or a.status.unique()) & a.data_basis.isin(fb or a.data_basis.unique())]
    if fr:
        v = v[v.rule.isin(fr)]
    if fv:
        v = v[v.severity.isin(fv)]
    ui.card_row([ui.kpi_card("Open alerts", (a.status == "Open").sum(), "count"),
                 ui.kpi_card("Critical / high open", (a.status.isin(["Open", "Acknowledged", "Investigating"])
                                                      & a.severity.isin(["critical", "high"])).sum(), "count"),
                 ui.kpi_card("Actual-data alerts", (a.data_basis == "actual").sum(), "count"),
                 ui.kpi_card("Synthetic-data alerts", (a.data_basis == "synthetic").sum(), "count", basis="synthetic")])
    show = v.assign(severity=v.severity.map(SEV))[["alert_id", "metric", "period", "detection_date", "severity",
                                                   "observed_value", "expected_value", "variance_pct", "data_basis",
                                                   "owner", "status"]]
    st.dataframe(show, hide_index=True, width="stretch", height=320,
                 column_config={"variance_pct": st.column_config.NumberColumn(format="percent"),
                                "observed_value": st.column_config.NumberColumn(format="%.4g"),
                                "expected_value": st.column_config.NumberColumn(format="%.4g")})
    ui.download_buttons(v, "alerts", "al_dl")
    if v.empty:
        return
    sel = st.selectbox("Alert details", v.alert_id, key="al_sel")
    r = a.set_index("alert_id").loc[sel]
    if r.data_basis == "synthetic":
        ui.synthetic_banner()
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown(f"**{r.metric}** · {r.period} · {SEV[r.severity]} · status **{r.status}** · owner {r.owner}")
        st.markdown(f"Observed **{r.observed_value:,.4g}** vs expected **{r.expected_value:,.4g}**"
                    + (f" ({r.variance_pct:+.1%})" if r.variance_pct == r.variance_pct and r.variance_pct is not None else ""))
        st.markdown(f"**Recommended investigation:** {r.recommended_investigation}")
        st.markdown("**Supporting evidence**")
        st.json(json.loads(r.evidence or "{}"), expanded=False)
        if r.resolution_note:
            st.markdown(f"**Notes:** {r.resolution_note}")
    with c2:
        if can(u.role, "manage_alerts"):
            with st.form("al_form"):
                to = st.selectbox("Move to", sorted(VALID_TRANSITIONS.get(r.status, set())))
                owners = sorted(set(OWNERS.values()) | {r.owner})
                owner = st.selectbox("Assign owner", owners, index=owners.index(r.owner))
                note = st.text_area("Note")
                if st.form_submit_button("Update alert", type="primary"):
                    try:
                        transition(sel, to, u.username, note, owner)
                        audit(u.username, u.role, f"alert_{to.lower()}", "alerts", sel, note)
                        st.success("Updated")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
        st.markdown("**History**")
        st.dataframe(app_query("select ts, user_name, from_status, to_status, note from alert_events where alert_id = ? "
                               "order by id", [sel]), hide_index=True, width="stretch")
