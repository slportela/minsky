"""Copy the organizer's raw CSV files into our bronze zone, incrementally.

Bronze is a byte-identical copy of what the source delivered. Each run:
  1. lists the source under data/ (the organizer bucket, read-only credentials from .env),
  2. skips files whose (key, ETag) is already in the bronze manifest,
  3. copies the rest, verifying MD5 (or size for multipart ETags),
  4. appends a manifest file for the run: bronze/_manifest/run=<run_id>.jsonl.

Re-running with no source changes copies nothing. A changed file (same key, new ETag)
is copied again; enable bucket versioning on the bronze bucket to keep the old bytes.

Usage:
  uv run python pipeline/ingest_bronze.py --dest data/bronze --dry-run            # local test
  uv run python pipeline/ingest_bronze.py --dest s3://my-bucket/bronze --profile my-aws-profile
  uv run python pipeline/ingest_bronze.py --dest data/bronze --tables branches customers
"""

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import boto3
from bank_data import BUCKET, DATA, _get_bytes, inventory

MANIFEST_DIR = "_manifest"


class LocalDest:
    def __init__(self, root):
        self.root = Path(root)

    def write(self, key, body, metadata):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def read_manifests(self):
        for p in sorted((self.root / MANIFEST_DIR).glob("*.jsonl")):
            yield from p.read_text().splitlines()

    def __str__(self):
        return str(self.root)


class S3Dest:
    def __init__(self, uri, profile):
        bucket, _, prefix = uri.removeprefix("s3://").partition("/")
        self.bucket, self.prefix = bucket, prefix.strip("/")
        self.s3 = boto3.Session(profile_name=profile).client("s3")

    def _key(self, key):
        return f"{self.prefix}/{key}" if self.prefix else key

    def write(self, key, body, metadata):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key), Body=body,
                           Metadata=metadata, ServerSideEncryption="AES256")

    def read_manifests(self):
        pages = self.s3.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self._key(MANIFEST_DIR) + "/")
        for page in pages:
            for o in page.get("Contents", []):
                body = self.s3.get_object(Bucket=self.bucket, Key=o["Key"])["Body"].read()
                yield from body.decode().splitlines()

    def __str__(self):
        return f"s3://{self.bucket}/{self.prefix}"


def already_ingested(dest):
    """(source_key, etag) pairs recorded by previous runs."""
    return {(r["source_key"], r["etag"]) for r in map(json.loads, dest.read_manifests())}


def multipart_etag(body, part_size):
    """S3 ETag of a multipart upload: MD5 of the concatenated part MD5s, plus '-<parts>'."""
    parts = [hashlib.md5(body[i:i + part_size]).digest() for i in range(0, len(body), part_size)]
    return f"{hashlib.md5(b''.join(parts)).hexdigest()}-{len(parts)}"


def verify(body, etag, size):
    """None if body matches the source object, else the reason it doesn't."""
    if len(body) != size:
        return f"size {len(body)} != {size}"
    if "-" not in etag:
        return None if hashlib.md5(body).hexdigest() == etag else "md5 mismatch"
    # 8 MiB is the boto3/AWS CLI default part size (matches the organizer's multipart files)
    return None if multipart_etag(body, 8 * 1024 * 1024) == etag else "multipart etag mismatch"


def copy_one(row, dest, run_id):
    body = _get_bytes(row.key)
    error = verify(body, row.etag, row.size)
    if error:
        raise ValueError(f"{row.key}: {error}")
    ingested_at = datetime.now(UTC).isoformat()
    dest.write(row.key, body, {"source-bucket": BUCKET, "source-etag": row.etag,
                               "source-last-modified": row.last_modified.isoformat(),
                               "ingested-at": ingested_at, "run-id": run_id})
    return {"run_id": run_id, "source_bucket": BUCKET, "source_key": row.key, "etag": row.etag,
            "size": row.size, "source_last_modified": row.last_modified.isoformat(),
            "dest": f"{dest}/{row.key}", "ingested_at": ingested_at}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", default=os.environ.get("BRONZE_URI"),
                    help="s3://bucket/prefix or a local folder (default: $BRONZE_URI)")
    ap.add_argument("--profile", default=os.environ.get("BRONZE_AWS_PROFILE"),
                    help="AWS profile that can write to --dest (default: $BRONZE_AWS_PROFILE)")
    ap.add_argument("--tables", nargs="*", help="only these tables (default: all under data/)")
    ap.add_argument("--start", help="only partitions on/after this date (YYYY-MM-DD)")
    ap.add_argument("--end", help="only partitions on/before this date (YYYY-MM-DD)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true", help="list what would be copied and exit")
    args = ap.parse_args(argv)
    if not args.dest:
        ap.error("--dest is required (or set BRONZE_URI)")

    dest = S3Dest(args.dest, args.profile) if args.dest.startswith("s3://") else LocalDest(args.dest)

    src = inventory()
    if args.tables:
        src = src[src["table"].isin(args.tables)]
    # dimension tables have no partition date: always in scope
    if args.start:
        src = src[src["partition_date"].isna() | (src["partition_date"] >= args.start)]
    if args.end:
        src = src[src["partition_date"].isna() | (src["partition_date"] <= args.end)]

    done = already_ingested(dest)
    todo = src[[(k, e) not in done for k, e in zip(src["key"], src["etag"], strict=True)]]
    print(f"source: s3://{BUCKET}/{DATA} · in scope: {len(src):,} files · "
          f"already in bronze: {len(src) - len(todo):,} · to copy: {len(todo):,} "
          f"({todo['size'].sum() / 1e6:,.1f} MB) → {dest}")
    if args.dry_run or todo.empty:
        if args.dry_run:
            print(todo.groupby("table").agg(files=("key", "size"), mb=("size", lambda s: round(s.sum() / 1e6, 1))))
        return 0

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    records, failures, t0 = [], [], time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        futures = {ex.submit(copy_one, row, dest, run_id): row.key for row in todo.itertuples()}
        for i, f in enumerate(as_completed(futures), 1):
            try:
                records.append(f.result())
            except Exception as e:  # keep going; failed files are retried on the next run
                failures.append(f"{futures[f]}: {e}")
            if i % 500 == 0:
                print(f"  {i:,}/{len(todo):,} files", flush=True)

    # the manifest is written last, so an interrupted run leaves no false "done" entries
    if records:
        body = "\n".join(json.dumps(r) for r in sorted(records, key=lambda r: r["source_key"])) + "\n"
        dest.write(f"{MANIFEST_DIR}/run={run_id}.jsonl", body.encode(), {"run-id": run_id})
    print(f"run {run_id}: copied {len(records):,} files in {time.time() - t0:.0f}s · failed {len(failures)}")
    for f in failures[:20]:
        print("  FAILED", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
