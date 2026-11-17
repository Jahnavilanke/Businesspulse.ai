"""Shared UI components, design tokens, cached data access and chart styling."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from businesspulse import db
from businesspulse.analytics import kpis

CSS = (Path(__file__).parent / "theme.css").read_text(encoding="utf-8")

# Validated categorical palette (fixed order - colour follows the entity, never its rank)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQUENTIAL = ["#e8f1fc", "#b9d3f5", "#7fb0ec", "#2a78d6", "#1a54a0", "#0d3366"]
DIVERGING = ["#c2410c", "#f2a37d", "#e5e4df", "#7fb0ec", "#1a54a0"]
INK, INK_2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#ebeae6"

pio.templates["bp"] = go.layout.Template(layout=go.Layout(
    font=dict(family="Inter, Segoe UI, sans-serif", size=13, color=INK_2),
    colorway=SERIES, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    xaxis=dict(gridcolor=GRID, linecolor=GRID, zeroline=False, ticks=""),
    yaxis=dict(gridcolor=GRID, linecolor="rgba(0,0,0,0)", zeroline=False, ticks=""),
    legend=dict(orientation="h", y=-0.12, yanchor="top", x=0, title_text=""),
    margin=dict(l=8, r=8, t=40, b=8), hoverlabel=dict(bgcolor="white", font_color=INK),
    hovermode="x unified", bargap=0.25,
))
pio.templates.default = "plotly_white+bp"


def style(fig: go.Figure, title: str | None = None, height: int = 340) -> go.Figure:
    fig.update_layout(height=height, title=dict(text=title, font=dict(size=15, color=INK), x=0, xanchor="left")
                      if title else None)
    fig.update_traces(selector=dict(type="scatter", mode="lines"), line=dict(width=2))
    fig.update_traces(selector=dict(type="bar"), marker_line_width=0)
    return fig


def chart(fig: go.Figure, key: str | None = None) -> None:
    st.plotly_chart(fig, width="stretch", key=key, config={"displaylogo": False})


# ------------------------------------------------------------------ data access (cached on warehouse mtime)
@st.cache_data(show_spinner=False, ttl=600)
def _q(sql: str, params: tuple, version: float) -> pd.DataFrame:
    return db.query_df(sql, list(params))


def q(sql: str, params: list | tuple = ()) -> pd.DataFrame:
    return _q(sql, tuple(params), db.warehouse_version())


@st.cache_data(show_spinner=False, ttl=600)
def _kpis(start: date, end: date, countries: tuple, categories: tuple, version: float) -> dict:
    return kpis.compute_kpis(start, end, list(countries) or None, list(categories) or None)


def kpi_values(start, end, countries=(), categories=()) -> dict:
    return _kpis(start, end, tuple(countries), tuple(categories), db.warehouse_version())


@st.cache_data(show_spinner=False, ttl=600)
def _cmp(start, end, kind, version):
    return kpis.kpi_comparison(start, end, kind)


def kpi_cmp(start, end, kind="prior") -> pd.DataFrame:
    return _cmp(start, end, kind, db.warehouse_version())


def has(schema: str, table: str) -> bool:
    return db.table_exists(schema, table)


# ------------------------------------------------------------------ layout pieces
def page_header(title: str, subtitle: str = "", basis: str | None = None) -> None:
    badge = ""
    if basis == "actual":
        badge = '<span class="bp-badge bp-actual">Actual data</span>'
    elif basis == "synthetic":
        badge = '<span class="bp-badge bp-synth">Synthetic demo data</span>'
    elif basis == "mixed":
        badge = '<span class="bp-badge bp-mixed">Actual + synthetic</span>'
    st.markdown(f'<div class="bp-header"><h1>{title}</h1>{badge}<p>{subtitle}</p></div>', unsafe_allow_html=True)


def synthetic_banner(text: str = "") -> None:
    st.markdown(
        '<div class="bp-banner">⚠️ <b>Synthetic demonstration data.</b> Generated, clearly-labelled data used to '
        "demonstrate the method. It is <b>not</b> the retailer's real performance and must not be used for "
        f"decisions. {text}</div>", unsafe_allow_html=True)


def kpi_card(label: str, value, unit: str, delta: float | None = None, direction: str = "higher_is_better",
             basis: str = "actual", help_text: str = "") -> str:
    v = value if unit == "text" else kpis.format_value(value, unit)
    d = ""
    if delta is not None and delta == delta:
        good = (delta >= 0) == (direction == "higher_is_better")
        arrow = "▲" if delta >= 0 else "▼"
        d = f'<div class="bp-delta {"up" if good else "down"}">{arrow} {delta:+.1%}<span> vs comparison</span></div>'
    tag = '<span class="bp-tag">synthetic</span>' if basis == "synthetic" else ""
    tag = '<span class="bp-tag na">unavailable</span>' if basis == "unavailable" else tag
    return (f'<div class="bp-card" title="{help_text}"><div class="bp-card-label">{label}{tag}</div>'
            f'<div class="bp-card-value">{v}</div>{d}</div>')


def card_row(cards: list[str]) -> None:
    st.markdown(f'<div class="bp-cards">{"".join(cards)}</div>', unsafe_allow_html=True)


def empty_state(title: str, body: str, icon: str = "📭") -> None:
    st.markdown(f'<div class="bp-empty"><div class="i">{icon}</div><b>{title}</b><p>{body}</p></div>',
                unsafe_allow_html=True)


def require_warehouse() -> bool:
    if not db.warehouse_exists() or not db.table_exists("gold", "fact_sales"):
        empty_state("No analytical data yet",
                    "Run the pipeline to ingest, validate and model the data: "
                    "<code>python -m businesspulse.pipeline.runner</code>", "🛠️")
        return False
    return True


def download_buttons(df: pd.DataFrame, name: str, key: str) -> None:
    c1, c2, _ = st.columns([1, 1, 6])
    c1.download_button("⬇ CSV", df.to_csv(index=False).encode("utf-8"), f"{name}.csv", "text/csv", key=f"{key}-csv")
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    c2.download_button("⬇ Excel", buf.getvalue(), f"{name}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"{key}-xlsx")


def searchable_table(df: pd.DataFrame, key: str, height: int = 380, column_config: dict | None = None) -> pd.DataFrame:
    term = st.text_input("Search table", key=f"{key}-search", placeholder="Filter rows containing…",
                         label_visibility="collapsed")
    view = df
    if term:
        mask = df.astype(str).apply(lambda c: c.str.contains(term, case=False, na=False)).any(axis=1)
        view = df[mask]
    st.dataframe(view, width="stretch", height=height, hide_index=True, column_config=column_config)
    st.caption(f"{len(view):,} of {len(df):,} rows")
    return view


def filters() -> dict:
    """Global filters stored in session state by app.py."""
    return st.session_state.get("filters", {})


def user():
    return st.session_state.get("user")
