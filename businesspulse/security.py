"""Role-based access control, demo identities and the read-only SQL guard."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from dataclasses import dataclass

import pandas as pd

from .config import settings

ROLES = ["Data Analyst", "Business Analyst", "Business Manager", "Administrator"]

# page -> roles allowed to view
PAGE_ACCESS = {
    "executive": ROLES, "sales": ROLES, "customers": ROLES, "marketing": ROLES, "inventory": ROLES,
    "predictive": ROLES, "insights": ROLES, "alerts": ROLES, "reports": ROLES, "catalog": ROLES,
    "operations": ["Data Analyst", "Administrator"],
    "data_analyst": ["Data Analyst", "Administrator"],
    "business_analyst": ["Business Analyst", "Business Manager", "Administrator"],
    "simulator": ["Business Analyst", "Business Manager", "Administrator"],
    "admin": ["Administrator"],
}
# action -> roles allowed
ACTIONS = {
    "run_sql": ["Data Analyst", "Administrator"],
    "edit_requirements": ["Business Analyst", "Administrator"],
    "edit_kpi_targets": ["Business Analyst", "Business Manager", "Administrator"],
    "edit_processes": ["Business Analyst", "Administrator"],
    "edit_decisions": ["Business Analyst", "Business Manager", "Administrator"],
    "approve_decisions": ["Business Manager", "Administrator"],
    "manage_alerts": ["Data Analyst", "Business Analyst", "Business Manager", "Administrator"],
    "save_scenarios": ["Business Analyst", "Business Manager", "Administrator"],
    "export": ROLES,
}

# Synthetic demonstration identities (local development only - no real people).
DEMO_USERS = {
    "dana.analyst": ("Dana (demo)", "Data Analyst"),
    "ben.ba": ("Ben (demo)", "Business Analyst"),
    "maya.manager": ("Maya (demo)", "Business Manager"),
    "alex.admin": ("Alex (demo)", "Administrator"),
}


@dataclass
class User:
    username: str
    display_name: str
    role: str


def can_view(role: str, page: str) -> bool:
    return role in PAGE_ACCESS.get(page, [])


def can(role: str, action: str) -> bool:
    return role in ACTIONS.get(action, [])


def verify_password(username: str, password: str) -> bool:
    """Password mode: hashes come from env vars BP_USER_<USERNAME>_SHA256 (never hardcoded)."""
    key = f"BP_USER_{username.replace('.', '_').upper()}_SHA256"
    expected = os.environ.get(key)
    if not expected:
        return False
    digest = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(digest, expected)


# ------------------------------------------------------------------ SQL guard
ALLOWED_START = re.compile(r"^\s*(with|select|describe|show|explain|summarize|pragma\s+table_info|from)\b", re.I)
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|create|alter|truncate|attach|detach|copy|export|import|install|load|"
    r"set|reset|call|checkpoint|vacuum|grant|revoke|begin|commit|rollback|pragma(?!\s+table_info))\b", re.I)
FILE_FUNCS = re.compile(r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|glob|parquet_scan|"
                        r"sniff_csv|httpfs|s3)\b", re.I)


def _strip(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"'(?:[^']|'')*'", "''", sql)   # ignore string literal contents


def check_sql(sql: str) -> tuple[bool, str]:
    body = _strip(sql).strip().rstrip(";")
    if not body:
        return False, "Empty query."
    if ";" in body:
        return False, "Only a single statement is allowed."
    if not ALLOWED_START.match(body):
        return False, "Only read-only queries (SELECT / WITH / DESCRIBE / SHOW / EXPLAIN) are allowed."
    m = FORBIDDEN.search(body)
    if m:
        return False, f"Keyword '{m.group(0)}' is not allowed in the read-only SQL Studio."
    m = FILE_FUNCS.search(body)
    if m:
        return False, f"File/network access ('{m.group(0)}') is disabled; query warehouse tables instead."
    return True, "ok"


def run_readonly_sql(sql: str, limit: int | None = None) -> tuple[pd.DataFrame, float]:
    """Defense in depth: statement guard + read-only DuckDB connection + external access disabled + row cap."""
    import duckdb

    ok, msg = check_sql(sql)
    if not ok:
        raise PermissionError(msg)
    limit = limit or settings.sql_row_limit
    con = duckdb.connect(str(settings.warehouse_path), read_only=True)
    try:
        con.execute("set enable_progress_bar = false")
        con.execute("set enable_external_access = false")
        con.execute("set lock_configuration = true")
        t0 = time.perf_counter()
        df = con.execute(f"select * from ({sql.strip().rstrip(';')}) as q limit {int(limit)}"
                         if not re.match(r"^\s*(describe|show|explain|summarize|pragma)", _strip(sql), re.I)
                         else sql).df()
        return df, (time.perf_counter() - t0) * 1000
    finally:
        con.close()
