"""Helpers to read the LATAM Bank dataset from S3."""

import csv
import io
import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import boto3
import pandas as pd
from botocore.exceptions import ResponseStreamingError
from dotenv import load_dotenv
from urllib3.exceptions import ProtocolError

ROOT = Path(__file__).resolve().parents[1]  # repo root: .env and data/ live there
load_dotenv(ROOT / ".env", override=True)

BUCKET = os.environ["S3_BUCKET_NAME"]
REGION = os.environ["AWS_REGION"]
DATA = "data/"
PARTITION_RE = r"year=(\d{4})/month=(\d{2})/day=(\d{2})"

s3 = boto3.client("s3", region_name=REGION)


@lru_cache(maxsize=1)
def inventory() -> pd.DataFrame:
    """All objects under data/, with table name and partition date."""
    rows = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=DATA):
        for o in page.get("Contents", []):
            rows.append({"key": o["Key"], "size": o["Size"], "last_modified": o["LastModified"],
                         "etag": o["ETag"].strip('"')})
    inv = pd.DataFrame(rows)
    inv = inv[~inv["key"].str.endswith("/")].copy()
    inv["table"] = inv["key"].str[len(DATA):].str.split("/").str[0].str.removesuffix(".csv")
    parts = inv["key"].str.extract(PARTITION_RE)
    inv["partition_date"] = pd.to_datetime(parts[0] + "-" + parts[1] + "-" + parts[2], errors="coerce")
    return inv.reset_index(drop=True)


def table_keys(table, start=None, end=None):
    """Keys of a table; for partitioned tables, filtered to [start, end]."""
    sub = inventory()
    sub = sub[sub["table"] == table]
    if start:
        sub = sub[sub["partition_date"] >= pd.Timestamp(start)]
    if end:
        sub = sub[sub["partition_date"] <= pd.Timestamp(end)]
    return sub.sort_values("key")["key"].tolist()


def _get_bytes(key, attempts=5, **get_kwargs):
    """GetObject + read, retrying dropped connections (botocore doesn't retry body reads)."""
    for i in range(attempts):
        try:
            return s3.get_object(Bucket=BUCKET, Key=key, **get_kwargs)["Body"].read()
        except (ResponseStreamingError, ProtocolError, ConnectionError):
            if i == attempts - 1:
                raise
            time.sleep(2 ** i)


def read_csv_key(key, nrows=None, **kwargs):
    """Read one CSV object. All columns as strings by default so nothing is silently coerced."""
    body = _get_bytes(key)
    kwargs.setdefault("dtype", str)
    kwargs.setdefault("keep_default_na", False)
    kwargs.setdefault("na_values", [""])
    df = pd.read_csv(io.BytesIO(body), encoding="utf-8-sig", nrows=nrows, **kwargs)
    m = pd.Series([key]).str.extract(PARTITION_RE).iloc[0]
    if m.notna().all():
        df["_partition_date"] = pd.Timestamp(f"{m[0]}-{m[1]}-{m[2]}")
    df["_source_key"] = key
    return df


def read_table(table, start=None, end=None, nrows=None, workers=16, **kwargs):
    """Read a whole table (or a date range of a partitioned one) in parallel."""
    keys = table_keys(table, start, end)
    if not keys:
        raise ValueError(f"No files for {table!r} in that range")
    with ThreadPoolExecutor(workers) as ex:
        frames = list(ex.map(lambda k: read_csv_key(k, nrows=nrows, **kwargs), keys))
    return pd.concat(frames, ignore_index=True)


CACHE = ROOT / "data" / "cache"


def load_table(table, columns=None, refresh=False, **kwargs):
    """Full table (all partitions) with a local Parquet cache in data/cache/.

    `columns` limits what is kept (useful for digital_events, ~10M rows); the cache
    file name includes the column selection so different selections don't collide.
    """
    suffix = "" if columns is None else "__" + "-".join(sorted(columns))
    path = CACHE / f"{table}{suffix}.parquet"
    if path.exists() and not refresh:
        return pd.read_parquet(path)
    if columns is not None:
        kwargs["usecols"] = lambda c, keep=set(columns): c in keep
    df = read_table(table, **kwargs)
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    return df


def read_header(key, nbytes=8192):
    """Column names of a CSV object, reading only its first bytes."""
    chunk = _get_bytes(key, Range=f"bytes=0-{nbytes - 1}")
    first_line = chunk.decode("utf-8-sig", errors="replace").splitlines()[0]
    return next(csv.reader([first_line]))


def headers(table, workers=32):
    """Header of every file of a table -> DataFrame(key, partition_date, columns)."""
    inv = inventory()
    sub = inv[inv["table"] == table].sort_values("key")
    with ThreadPoolExecutor(workers) as ex:
        cols = list(ex.map(read_header, sub["key"]))
    return pd.DataFrame({"key": sub["key"].values, "partition_date": sub["partition_date"].values,
                         "columns": [tuple(c) for c in cols]})
