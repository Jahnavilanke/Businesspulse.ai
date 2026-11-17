-- Application-state schema (SQLite locally; DDL is ANSI-compatible for PostgreSQL).
-- Holds business-user–authored records. Analytical data lives in the DuckDB warehouse.

create table if not exists audit_log (
    id integer primary key autoincrement,
    ts text not null default current_timestamp,
    user_name text not null,
    role text not null,
    action text not null,
    entity text,
    entity_id text,
    detail text
);

create table if not exists saved_queries (
    id integer primary key autoincrement,
    name text not null unique,
    description text,
    sql_text text not null,
    owner text not null,
    created_at text not null default current_timestamp
);

create table if not exists query_history (
    id integer primary key autoincrement,
    ts text not null default current_timestamp,
    user_name text not null,
    sql_text text not null,
    status text not null,           -- success | error | blocked
    row_count integer,
    duration_ms real,
    error text
);

create table if not exists requirements (
    req_id text primary key,        -- e.g. BR-001, FR-004, NFR-002
    req_type text not null,         -- Business | Functional | Non-Functional | Stakeholder | Objective | Scope | Assumption | Constraint
    title text not null,
    description text,
    stakeholder text,
    priority text not null default 'Should',   -- MoSCoW
    owner text,
    status text not null default 'Draft',      -- Draft | Approved | In Progress | Done | Rejected
    acceptance_criteria text,
    linked_kpi text,
    is_demo integer not null default 0,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create table if not exists kpi_registry (
    kpi_id text primary key,        -- matches businesspulse.analytics.kpis metric keys
    name text not null,
    business_definition text not null,
    calculation text not null,
    unit text not null,
    direction text not null default 'higher_is_better',
    target_value real,
    target_basis text,              -- how the target was set (documented, user-editable)
    department text,
    frequency text,
    data_basis text not null default 'actual',  -- actual | synthetic | unavailable
    updated_by text,
    updated_at text not null default current_timestamp
);

create table if not exists processes (
    process_id text primary key,
    name text not null,
    department text,
    owner text,
    current_state text,
    steps_json text,                -- [{"step":..,"owner":..,"duration_hrs":..,"bottleneck":bool}]
    bottlenecks text,
    issues text,
    mermaid text,
    is_demo integer not null default 0,
    updated_at text not null default current_timestamp
);

create table if not exists improvements (
    improvement_id text primary key,
    process_id text references processes(process_id) on delete cascade,
    title text not null,
    proposal text,
    expected_impact text,
    impact_metric text,
    owner text,
    status text not null default 'Proposed',   -- Proposed | Approved | In Progress | Implemented | Rejected
    target_date text,
    is_demo integer not null default 0,
    updated_at text not null default current_timestamp
);

create table if not exists decisions (
    decision_id text primary key,
    problem text not null,
    evidence text,
    evidence_source text,           -- e.g. alert id, root-cause run, saved query
    proposed_action text,
    expected_outcome text,
    owner text,
    priority text default 'Medium',
    estimated_cost real,
    success_metric text,
    baseline_value real,
    target_value real,
    status text not null default 'Proposed',   -- Proposed | Approved | In Progress | Implemented | Evaluated | Rejected
    actual_outcome text,
    actual_value real,
    is_demo integer not null default 0,
    created_at text not null default current_timestamp,
    updated_at text not null default current_timestamp
);

create table if not exists alerts (
    alert_id text primary key,      -- deterministic: rule + metric + period
    rule text not null,
    metric text not null,
    period text not null,
    detection_date text not null,
    severity text not null,         -- low | medium | high | critical
    observed_value real,
    expected_value real,
    variance_pct real,
    evidence text,
    recommended_investigation text,
    data_basis text not null default 'actual',
    owner text,
    status text not null default 'Open',  -- Open | Acknowledged | Investigating | Resolved | Dismissed
    resolution_note text,
    updated_at text not null default current_timestamp
);

create table if not exists alert_events (
    id integer primary key autoincrement,
    alert_id text not null references alerts(alert_id) on delete cascade,
    ts text not null default current_timestamp,
    user_name text not null,
    from_status text,
    to_status text,
    note text
);

create table if not exists scenarios (
    scenario_id integer primary key autoincrement,
    name text not null,
    created_by text not null,
    created_at text not null default current_timestamp,
    assumptions_json text not null,
    results_json text not null
);
