"""Headless render of every dashboard page for each role (requires a built warehouse)."""
from __future__ import annotations

import pytest

from businesspulse.db import table_exists, warehouse_exists

pytestmark = pytest.mark.skipif(not (warehouse_exists() and table_exists("gold", "fact_sales")),
                                reason="warehouse not built - run the pipeline first")

VIEWS = ["executive", "sales", "customers", "marketing", "inventory", "predictive", "data_analyst",
         "business_analyst", "simulator", "insights", "alerts", "operations", "catalog", "reports"]


def _script(view: str):
    def page():
        import importlib
        import sys
        from pathlib import Path

        import streamlit as st
        sys.path.insert(0, str(Path.cwd()))
        from businesspulse.analytics.kpis import data_range
        from businesspulse.security import User
        d0, d1 = data_range()
        import pandas as pd
        st.session_state.setdefault("user", User("alex.admin", "Alex (demo)", "Administrator"))
        st.session_state.setdefault("filters", {"start": (pd.Timestamp(d1) - pd.DateOffset(years=1)).date(), "end": d1,
                                                "countries": [], "categories": [], "department": "All"})
        mod = importlib.import_module(f"dashboard.views.{VIEW}")
        mod.render()
    return page.__code__.co_consts, page


@pytest.mark.parametrize("view", VIEWS)
def test_view_renders_without_exception(view):
    from streamlit.testing.v1 import AppTest

    src = f'''
import importlib, sys
from pathlib import Path
import pandas as pd
import streamlit as st
from businesspulse.analytics.kpis import data_range
from businesspulse.security import User
d0, d1 = data_range()
st.session_state.setdefault("user", User("alex.admin", "Alex (demo)", "Administrator"))
st.session_state.setdefault("filters", {{"start": (pd.Timestamp(d1) - pd.DateOffset(years=1)).date(), "end": d1,
                                        "countries": [], "categories": [], "department": "All"}})
importlib.import_module("dashboard.views.{view}").render()
'''
    at = AppTest.from_string(src, default_timeout=180)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    errors = [e.value for e in at.error]
    assert not errors, errors
