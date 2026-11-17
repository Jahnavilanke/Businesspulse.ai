PY ?= .venv/bin/python

.PHONY: setup pipeline pipeline-fast app test docs reset up down

setup:
	python3 -m venv .venv && $(PY) -m pip install -r requirements-dev.txt && cp -n .env.example .env || true

pipeline:
	$(PY) -m businesspulse.pipeline.runner

pipeline-fast:
	$(PY) -m businesspulse.pipeline.runner --skip-ml

app:
	$(PY) -m streamlit run app.py

test:
	$(PY) -m pytest tests -q

docs:
	BP_WAREHOUSE_PATH=$(PWD)/data/warehouse/businesspulse.duckdb BP_LAKE_DIR=$(PWD)/data/lake \
	  .venv/bin/dbt docs generate --project-dir dbt --profiles-dir dbt && $(PY) -m businesspulse.catalog

reset:
	rm -rf data/lake data/warehouse data/state data/exports data/mlruns data/lineage dbt/target

up:
	docker compose up -d --build

down:
	docker compose down
