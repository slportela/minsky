# Data pipeline: organizer S3 -> bronze (own S3) -> local mirror -> silver (dbt-duckdb) -> lake (own S3).
# Only BRONZE_URI and BRONZE_AWS_PROFILE are read from .env, so the AWS CLI never picks up
# the organizer keys that .env also holds.

BRONZE_URI := $(shell grep '^BRONZE_URI=' .env 2>/dev/null | cut -d= -f2-)
PROFILE    := $(shell grep '^BRONZE_AWS_PROFILE=' .env 2>/dev/null | cut -d= -f2-)
LAKE_URI   := $(patsubst %/bronze,%,$(BRONZE_URI))
AWS        := AWS_PROFILE=$(PROFILE) aws

.PHONY: help setup lock lock-check lint typecheck test frontend-check eval-check ci up down logs demo-plan demo-apply pipeline bronze mirror silver publish docs

# ---- Development ---------------------------------------------------------------------------

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

setup:  ## install Python and frontend dependencies, and git hooks
	uv sync --frozen --all-packages
	npm --prefix frontend ci --no-audit --no-fund
	uv run --frozen pre-commit install

lint:  ## ruff lint + format check
	uv run --frozen ruff check .
	uv run --frozen ruff format --check evals tests backend scripts

typecheck:  ## pyright on typed packages
	uv run --frozen pyright

test:  ## unit tests: eval harness + backend (policy, tools, API)
	uv run --frozen pytest

lock:  ## re-resolve dependencies (uv + npm) and rewrite lock files to public registry URLs
	uv lock
	npm --prefix frontend install --package-lock-only --no-audit --no-fund
	python3 scripts/public_lockfiles.py || python3 scripts/public_lockfiles.py --check

lock-check:  ## fail if a lock file points at a non-public registry
	python3 scripts/public_lockfiles.py --check

frontend-check:  ## frontend type check
	npm --prefix frontend run typecheck

eval-check:  ## validate every eval case and the case set (schema, leakage, coverage)
	uv run --frozen python -m evals.checks evals/cases --prompts prompts

ci: lock-check lint typecheck test eval-check frontend-check  ## everything a PR must pass (runs locally; no external CI service)

# ---- Run the system (see infra/README.md) ------------------------------------------------------

COMPOSE_LOCAL := docker compose -f compose.yaml -f compose.local.yaml
DEMO          := tofu -chdir=infra/tofu/envs/demo

up:  ## start the full stack locally (https://localhost)
	$(COMPOSE_LOCAL) up -d --build

down:  ## stop the local stack (volumes are kept)
	$(COMPOSE_LOCAL) down

logs:  ## follow the local stack logs
	$(COMPOSE_LOCAL) logs -f

demo-plan:  ## plan the AWS demo environment (one EC2 + compose)
	$(DEMO) init -input=false
	$(DEMO) plan

demo-apply:  ## create/update the AWS demo environment
	$(DEMO) apply

# ---- Data pipeline -------------------------------------------------------------------------

pipeline: bronze mirror silver publish  ## bronze, mirror and publish are incremental; silver is a full rebuild (~90 s)

bronze:  ## copy new/changed organizer files into our bronze bucket
	uv run python pipeline/ingest_bronze.py

mirror:  ## sync bronze to data/bronze (dbt reads local files: S3 per-file latency is too high)
	$(AWS) s3 sync $(BRONZE_URI) data/bronze --only-show-errors

silver:  ## generate models from the data dictionary and build + test them
	uv run python pipeline/transform/generate_silver.py
	mkdir -p data/lake/silver
	cd pipeline/transform && uv run dbt build --profiles-dir .

publish:  ## upload silver Parquet to the lake
	$(AWS) s3 sync data/lake/silver $(LAKE_URI)/silver --delete --only-show-errors

docs:  ## dbt docs with lineage at http://localhost:8080
	cd pipeline/transform && uv run dbt docs generate --profiles-dir . && uv run dbt docs serve --profiles-dir .
