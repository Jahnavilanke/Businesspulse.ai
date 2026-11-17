from __future__ import annotations

import json

import streamlit as st
import yaml

from businesspulse import catalog
from businesspulse.analytics.kpis import metric_catalog
from businesspulse.config import PROJECT_ROOT
from dashboard import components as ui


def render() -> None:
    ui.page_header("Data Catalog & Governance", "Sources and provenance, data dictionary, glossary, metric "
                   "definitions and data contracts.")
    t1, t2, t3, t4, t5 = st.tabs(["Sources & provenance", "Data dictionary", "Business glossary", "Metric definitions",
                                  "Data contracts"])
    with t1:
        src = yaml.safe_load((PROJECT_ROOT / "config" / "sources.yml").read_text(encoding="utf-8"))["sources"]
        for key, s in src.items():
            tag = "🧪 SYNTHETIC" if s.get("is_synthetic") else "✅ Real public data"
            with st.expander(f"{tag} · {s['title']}", expanded=not s.get("is_synthetic")):
                st.markdown(f"**Provider:** {s.get('provider')}  \n**License:** {s.get('license')}  \n"
                            f"**Coverage:** {s.get('coverage')}  \n**Grain:** {s.get('grain')}"
                            + (f"  \n**URL:** {s['url']}" if s.get("url") else "")
                            + (f"  \n**DOI:** {s['doi']}" if s.get("doi") else ""))
                if s.get("description"):
                    st.write(s["description"])
                st.markdown("**Limitations**\n" + "\n".join(f"- {x}" for x in s.get("limitations", [])))
        man = PROJECT_ROOT / "data" / "landing" / "uci_online_retail_ii" / "_manifest.json"
        if man.exists():
            st.markdown("**Acquisition manifest (UCI)**")
            st.json(json.loads(man.read_text(encoding="utf-8")), expanded=False)
    with t2:
        dd = catalog.data_dictionary()
        if dd.empty:
            ui.empty_state("Warehouse not built", "Run the pipeline.")
        else:
            layer = st.multiselect("Layer", ["bronze", "silver", "gold"], default=["gold"], key="dd_layer")
            v = dd[dd.table.str.split(".").str[0].isin(layer)]
            ui.searchable_table(v, "dd", 480)
            ui.download_buttons(v, "data_dictionary", "dd_dl")
    with t3:
        st.dataframe(catalog.glossary(), hide_index=True, width="stretch")
    with t4:
        st.dataframe(metric_catalog(), hide_index=True, width="stretch")
        st.caption("Python metric layer: businesspulse/analytics/kpis.py · SQL macros: dbt/macros/metrics.sql · "
                   "DAX equivalents: docs/powerbi/measures.dax")
    with t5:
        for f in sorted((PROJECT_ROOT / "contracts").glob("*.yml")):
            st.markdown(f"**{f.name}**")
            st.code(f.read_text(encoding="utf-8"), language="yaml")
