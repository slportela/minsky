# Data pipeline: organizer S3 -> bronze (own S3) -> local mirror -> silver (dbt-duckdb) -> lake (own S3).
# Only BRONZE_URI and BRONZE_AWS_PROFILE are read from .env, so the AWS CLI never picks up
# the organizer keys that .env also holds.

BRONZE_URI := $(shell grep '^BRONZE_URI=' .env | cut -d= -f2-)
PROFILE    := $(shell grep '^BRONZE_AWS_PROFILE=' .env | cut -d= -f2-)
LAKE_URI   := $(patsubst %/bronze,%,$(BRONZE_URI))
AWS        := AWS_PROFILE=$(PROFILE) aws

.PHONY: pipeline bronze mirror silver publish docs

pipeline: bronze mirror silver publish  ## bronze, mirror and publish are incremental; silver is a full rebuild (~90 s)

bronze:  ## copy new/changed organizer files into our bronze bucket
	uv run python ingest_bronze.py

mirror:  ## sync bronze to data/bronze (dbt reads local files: S3 per-file latency is too high)
	$(AWS) s3 sync $(BRONZE_URI) data/bronze --only-show-errors

silver:  ## generate models from the data dictionary and build + test them
	uv run python transform/generate_silver.py
	mkdir -p data/lake/silver
	cd transform && uv run dbt build --profiles-dir .

publish:  ## upload silver Parquet to the lake
	$(AWS) s3 sync data/lake/silver $(LAKE_URI)/silver --delete --only-show-errors

docs:  ## dbt docs with lineage at http://localhost:8080
	cd transform && uv run dbt docs generate --profiles-dir . && uv run dbt docs serve --profiles-dir .
