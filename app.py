"""BusinessPulse AI - Streamlit entry point.

    streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from businesspulse import db  # noqa: E402
from businesspulse.bundle import ensure_data  # noqa: E402
from businesspulse.business_analysis.store import audit, seed_defaults  # noqa: E402
from businesspulse.config import settings  # noqa: E402
from businesspulse.security import DEMO_USERS, User, can_view, verify_password  # noqa: E402
from dashboard import components as ui  # noqa: E402
from dashboard.views import (alerts, business_analyst, catalog, customers, data_analyst, executive,  # noqa: E402
                             insights, inventory, marketing, operations, predictive, reports, sales, simulator)

st.set_page_config(page_title="BusinessPulse AI", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
st.markdown(f"<style>{ui.CSS}</style>", unsafe_allow_html=True)


@st.cache_resource(show_spinner="Loading the prebuilt warehouse (first start only)...")
def _bootstrap() -> bool:
    ensure_data()  # hosted deployments: fetch the CI-built bundle when no local warehouse exists
    db.migrate_app_db()
    seed_defaults()
    return True


_bootstrap()


def login() -> None:
    with st.sidebar:
        st.markdown("### 📈 BusinessPulse AI")
        if settings.auth_mode == "password":
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.button("Sign in", type="primary") and username in DEMO_USERS:
                if verify_password(username, password):
                    name, role = DEMO_USERS[username]
                    st.session_state.user = User(username, name, role)
                    audit(username, role, "login", "session")
                    st.rerun()
                st.error("Invalid credentials")
        else:
            st.caption("Local demo mode - synthetic identities, no real accounts.")
            options = {f"{n} · {r}": u for u, (n, r) in DEMO_USERS.items()}
            choice = st.selectbox("Sign in as", list(options), index=0)
            if st.button("Enter workspace", type="primary", width="stretch"):
                u = options[choice]
                name, role = DEMO_USERS[u]
                st.session_state.user = User(u, name, role)
                audit(u, role, "login", "session", detail="demo mode")
                st.rerun()
    ui.page_header("BusinessPulse AI", "Business intelligence, analytics & decision intelligence for multi-channel retail.")
    st.markdown("""
**Choose a demo identity in the sidebar.** Roles control which workspaces and actions are available:

| Role | Workspaces |
|---|---|
| Data Analyst | Dashboards, Data Analyst workspace (EDA, SQL Studio, statistics), Data Operations |
| Business Analyst | Dashboards, Business Analyst workspace, Simulator, decisions & KPI targets |
| Business Manager | Dashboards, BA workspace (approve decisions, set targets), Simulator |
| Administrator | Everything, incl. audit log |

Primary data: **UCI Online Retail II** (real UK online-retailer transactions, CC BY 4.0).
Inventory and marketing modules use **clearly-labelled synthetic** demonstration data.
""")


def sidebar_filters() -> None:
    if not (db.warehouse_exists() and db.table_exists("gold", "fact_sales")):
        st.session_state.filters = {}
        return
    from businesspulse.analytics.kpis import data_range

    d0, d1 = data_range()
    default_start = (pd.Timestamp(d1) - pd.DateOffset(years=1) + pd.Timedelta(days=1)).date()
    with st.sidebar:
        st.markdown("#### Filters")
        rng = st.date_input("Date range", (default_start, d1), min_value=d0, max_value=d1, key="f_dates")
        start, end = (rng if isinstance(rng, tuple) and len(rng) == 2 else (default_start, d1))
        countries = ui.q("select distinct country_raw from gold.fact_sales order by 1")["country_raw"].tolist()
        cats = ui.q("select distinct category from gold.dim_product where category <> 'Non-product' order by 1")[
            "category"].tolist()
        sel_c = st.multiselect("Countries", countries, key="f_countries", placeholder="All countries")
        sel_k = st.multiselect("Product categories", cats, key="f_categories", placeholder="All categories")
        dept = st.selectbox("Department", ["All", "Sales", "Marketing", "Operations", "CRM", "Supply Chain", "Finance"],
                            key="f_dept")
        st.caption(f"Data available {d0} → {d1}")
    st.session_state.filters = {"start": start, "end": end, "countries": sel_c, "categories": sel_k, "department": dept}


if "user" not in st.session_state:
    login()
    st.stop()

user: User = st.session_state.user
with st.sidebar:
    st.markdown(f"**{user.display_name}**  \n<span style='opacity:.75'>{user.role}</span>", unsafe_allow_html=True)
    if st.button("Sign out", width="stretch"):
        del st.session_state["user"]
        st.rerun()
sidebar_filters()

PAGES = [
    ("Dashboards", [("executive", "Executive Dashboard", "📊", executive.render),
                    ("sales", "Sales Analytics", "🛒", sales.render),
                    ("customers", "Customer Analytics", "👥", customers.render),
                    ("marketing", "Marketing Analytics", "📣", marketing.render),
                    ("inventory", "Inventory Analytics", "📦", inventory.render),
                    ("predictive", "Predictive Analytics", "🔮", predictive.render)]),
    ("Workspaces", [("data_analyst", "Data Analyst Workspace", "🧪", data_analyst.render),
                    ("business_analyst", "Business Analyst Workspace", "🧭", business_analyst.render),
                    ("simulator", "Business Impact Simulator", "🎛️", simulator.render),
                    ("insights", "AI Insights", "💡", insights.render),
                    ("alerts", "Alerts", "🚨", alerts.render)]),
    ("Data & Governance", [("operations", "Data Operations", "⚙️", operations.render),
                           ("catalog", "Data Catalog & Lineage", "📚", catalog.render),
                           ("reports", "Reports & Exports", "📄", reports.render)]),
]


def _wrap(fn, key):
    def run():
        try:
            fn()
        except FileNotFoundError as exc:
            ui.empty_state("Data not available", str(exc), "🛠️")
        except Exception as exc:  # friendly error instead of a stack trace
            st.error(f"Something went wrong on this page: {exc}")
            with st.expander("Technical details"):
                st.exception(exc)
    run.__name__ = f"page_{key}"
    return run


nav = {section: [st.Page(_wrap(fn, key), title=title, icon=icon, url_path=key)
                 for key, title, icon, fn in pages if can_view(user.role, key)]
       for section, pages in PAGES}
nav = {k: v for k, v in nav.items() if v}
st.navigation(nav, position="sidebar").run()
