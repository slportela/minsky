# Data pipeline: organizer S3 -> bronze (own S3) -> local mirror -> silver (dbt-duckdb) -> lake (own S3).
# Only BRONZE_URI and BRONZE_AWS_PROFILE are read from .env, so the AWS CLI never picks up
# the organizer keys that .env also holds.

BRONZE_URI := $(shell grep '^BRONZE_URI=' .env 2>/dev/null | cut -d= -f2-)
PROFILE    := $(shell grep '^BRONZE_AWS_PROFILE=' .env 2>/dev/null | cut -d= -f2-)
LAKE_URI   := $(patsubst %/bronze,%,$(BRONZE_URI))
AWS        := AWS_PROFILE=$(PROFILE) aws

.PHONY: demo-reset demo-sessions help setup lock-check lint typecheck test llm-smoke llm-smoke-tools regression-smoke eval-smoke eval-smoke-agentic eval-live-estimate eval-live frontend-check eval-check router ci model-prices up down logs demo-plan demo-apply pipeline bronze mirror silver gold publish docs freshness-check

EVAL_CAP_USD ?= 1
EVAL_AGENT_MODE ?= workflow  # workflow | agentic (docs/agentic_dispute_agent.md)
EVAL_TRIALS ?= 3
EVAL_WORKERS ?= 1  # trials run in this many processes (1 to 16); one run folder, one shared spend cap
EVAL_TIMEOUT_S ?= 300
EVAL_IDS ?=

# ---- Development ---------------------------------------------------------------------------

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-15s %s\n", $$1, $$2}'

setup:  ## install Python and frontend dependencies, and git hooks
	uv sync --all-packages
	npm --prefix frontend install --no-audit --no-fund
	uv run pre-commit install

lint:  ## ruff lint + format check
	uv run ruff check .
	uv run ruff format --check evals tests backend ml

typecheck:  ## pyright on typed packages
	uv run pyright

test:  ## unit tests: eval harness + backend (policy, tools, API)
	uv run pytest

frontend-check:  ## frontend type check
	npm --prefix frontend run typecheck
	npm --prefix frontend run test:api

llm-smoke:  ## one real call to the configured model (MINSKY_LLM_* in .env): key, endpoint, pinned model
	MINSKY_ENVIRONMENT=local uv run python -m minsky_api.llm.smoke

llm-smoke-tools:  ## the plain call plus the tool round trip the agentic search needs (a few cents)
	MINSKY_ENVIRONMENT=local uv run python -m minsky_api.llm.smoke --tools

eval-check:  ## validate every eval case and the case set (schema, leakage, coverage)
	uv run python -m evals.checks evals/cases --prompts prompts

eval-smoke:  ## partial offline dev smoke with durable evidence (scripted extraction)
	uv run python -m evals.runner --include-drafts

eval-smoke-agentic:  ## the same smoke through the tool-using agent (scripted agent and reactive customer; docs/agentic_dispute_agent.md)
	uv run python -m evals.runner --include-drafts --agent-mode agentic

# Token prices for the pinned model come from evals/model_prices.py, so one dated table feeds every
# run instead of a rate retyped per command. The runner still gets both prices as explicit flags.
# Force a price (a change the table does not have yet) by setting both on the command line:
#   make eval-live-estimate EVAL_INPUT_RATE=0.10 EVAL_OUTPUT_RATE=0.50
EVAL_PRICE_FLAGS = $(if $(and $(EVAL_INPUT_RATE),$(EVAL_OUTPUT_RATE)),--input-usd-per-million $(EVAL_INPUT_RATE) --output-usd-per-million $(EVAL_OUTPUT_RATE),$(shell MINSKY_ENVIRONMENT=local uv run python -m evals.model_prices --flags))

# EVAL_AGENT_MODE=agentic runs the tool-using agent; its estimate is about 20 times the workflow's, so raise the cap
# (EVAL_CAP_USD=3). EVAL_IDS=case-a,case-b narrows the run; EVAL_TRIALS sets the trials per case.
eval-live-estimate:  ## estimate a real dev run; token prices come from evals/model_prices.py
	uv run python -m evals.runner --include-drafts --extractor real --agent-mode $(EVAL_AGENT_MODE) --trials $(EVAL_TRIALS) --workers $(EVAL_WORKERS) $(if $(EVAL_IDS),--ids $(EVAL_IDS)) --timeout-s $(EVAL_TIMEOUT_S) --max-cost-usd $(EVAL_CAP_USD) $(EVAL_PRICE_FLAGS) --estimate-only

eval-live:  ## SPENDS MONEY: run the dev cases with the real model (key in .env); same variables as eval-live-estimate
	MINSKY_ENVIRONMENT=local uv run python -m evals.runner --include-drafts --extractor real --agent-mode $(EVAL_AGENT_MODE) --trials $(EVAL_TRIALS) --workers $(EVAL_WORKERS) $(if $(EVAL_IDS),--ids $(EVAL_IDS)) --timeout-s $(EVAL_TIMEOUT_S) --max-cost-usd $(EVAL_CAP_USD) $(EVAL_PRICE_FLAGS)

model-prices:  ## show the recorded token prices for the pinned model
	@MINSKY_ENVIRONMENT=local uv run python -m evals.model_prices

regression-smoke:  ## offline chat regressions (scripted extraction; no provider spend)
	uv run python -m evals.regression_smoke --output evals/runs/pr24-regressions.json

router:  ## train and evaluate the learned router; writes ml/reports and the backend artifact
	uv run python -m ml.router.generate
	uv run python -m ml.router.train

lock-check:  ## lockfiles resolve only from public registries (a private mirror breaks setup for everyone else)
	@bad=$$( grep -nE '(registry|url) = "https://' uv.lock | grep -vE '"https://(pypi\.org|files\.pythonhosted\.org)/'; \
		grep -nE '"resolved": "https://' frontend/package-lock.json | grep -v '"https://registry\.npmjs\.org/' ); \
	if [ -n "$$bad" ]; then echo "$$bad" | head -5; echo "lockfile points at a non-public registry"; exit 1; fi

ci: lock-check lint typecheck test eval-check regression-smoke eval-smoke eval-smoke-agentic frontend-check  ## everything a PR must pass (runs locally; no external CI service)

# ---- Run the system (see infra/README.md) ------------------------------------------------------

COMPOSE_LOCAL := docker compose -f compose.yaml -f compose.local.yaml
DEMO          := tofu -chdir=infra/tofu/envs/demo

demo-sessions:  ## print demo customer + agent-console credentials for .env (needs make gold)
	uv run python infra/demo_sessions.py

demo-reset:  ## delete every case (disputes, handoffs, blocks, audit, queue) so the demo can be replayed
	$(COMPOSE_LOCAL) exec postgres psql -U $${POSTGRES_USER:-minsky} -d $${POSTGRES_DB:-minsky} -c "DROP SCHEMA IF EXISTS cases CASCADE"
	$(COMPOSE_LOCAL) restart api

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

pipeline: bronze mirror silver gold publish  ## bronze, mirror and publish are incremental; silver (~90 s) and gold (~45 s) rebuild in full

bronze:  ## copy new/changed organizer files into our bronze bucket
	uv run python pipeline/ingest_bronze.py

mirror:  ## sync bronze to data/bronze (dbt reads local files: S3 per-file latency is too high)
	$(AWS) s3 sync $(BRONZE_URI) data/bronze --only-show-errors

silver:  ## generate models from the data dictionary and build + test them
	uv run python pipeline/transform/generate_silver.py
	mkdir -p data/lake/silver
	cd pipeline/transform && uv run dbt build --profiles-dir . --select +tag:silver

gold:  ## build + test the bank read models, export them to the lake and load them into Postgres (make up first)
	mkdir -p data/lake/gold
	cd pipeline/transform && uv run dbt build --profiles-dir . --select tag:gold
	uv run python pipeline/load_gold.py

freshness-check:  ## fail if ops.load_runs is older than MINSKY_FRESHNESS_MAX_AGE_HOURS (default 168)
	uv run python pipeline/check_freshness.py

publish:  ## upload silver and gold Parquet to the lake
	$(AWS) s3 sync data/lake/silver $(LAKE_URI)/silver --delete --only-show-errors
	$(AWS) s3 sync data/lake/gold $(LAKE_URI)/gold --delete --only-show-errors

docs:  ## dbt docs with lineage at http://localhost:8080
	cd pipeline/transform && uv run dbt docs generate --profiles-dir . && uv run dbt docs serve --profiles-dir .
