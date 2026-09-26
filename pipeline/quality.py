"""Data-quality checks of the LATAM Bank tables against data_dictionary.TABLES."""

import pandas as pd

META = ["_partition_date", "_source_key"]


def data_cols(df):
    return [c for c in df.columns if c not in META]


def summary(df, table, spec):
    """One row of headline metrics for a table."""
    cols = data_cols(df)
    pk = spec["pk"]
    pk_dup = df.duplicated(pk, keep=False)
    exact_dup = df.duplicated(cols, keep="first")
    nn = [c for c in spec["not_null"] if c in df.columns]
    return {
        "table": table,
        "rows": len(df),
        "rows_doc": spec["rows"],
        "rows_vs_doc_%": round(100 * (len(df) / spec["rows"] - 1), 2),
        "exact_dup_rows": int(exact_dup.sum()),
        "exact_dup_%": round(100 * exact_dup.mean(), 2),
        "pk_dup_keys": int(df.loc[pk_dup, pk].drop_duplicates().shape[0]),
        # same PK, different content: the real conflict (not a harmless re-send)
        "pk_conflicts": int(df.loc[pk_dup].drop_duplicates(cols).duplicated(pk).sum()),
        "not_null_violations": int(df[nn].isna().sum().sum()),
        "null_%_nullable_cols": round(100 * df[[c for c in cols if c not in nn]].isna().mean().mean(), 2)
        if len(cols) > len(nn) else None,
    }


def not_null_violations(df, spec):
    nn = [c for c in spec["not_null"] if c in df.columns]
    s = df[nn].isna().sum()
    return s[s > 0].sort_values(ascending=False)


def null_rates(df):
    return (df[data_cols(df)].isna().mean() * 100).round(2).sort_values(ascending=False)


def enum_violations(df, spec, top=10):
    """Values present in the data that the dictionary does not list."""
    out = []
    for col, allowed in spec.get("enums", {}).items():
        if col not in df.columns:
            continue
        vc = df[col].dropna().value_counts()
        bad = vc[~vc.index.isin(allowed)]
        if len(bad):
            out.append({
                "column": col,
                "documented": allowed,
                "unexpected_values": dict(bad.head(top)),
                "unexpected_%": round(100 * bad.sum() / vc.sum(), 2),
            })
    return pd.DataFrame(out)


def range_violations(df, spec):
    out = []
    for col, (lo, hi) in spec.get("ranges", {}).items():
        if col not in df.columns:
            continue
        x = pd.to_numeric(df[col], errors="coerce")
        bad = x[(x < lo) | (x > hi)]
        out.append({"column": col, "range": (lo, hi), "min": x.min(), "max": x.max(),
                    "violations": len(bad), "non_numeric": int((x.isna() & df[col].notna()).sum())})
    return pd.DataFrame(out)


def arrival_lag(df, spec):
    """Days between the event timestamp and the partition it landed in.

    0 = on time; >0 = arrived late (event from an earlier day); <0 = event after its partition.
    Also flags rows whose process_date column disagrees with the partition folder.
    """
    out = pd.DataFrame(index=df.index)
    part = df["_partition_date"]
    if spec.get("event_ts"):
        ev = pd.to_datetime(df[spec["event_ts"]], errors="coerce").dt.normalize()
        out["lag_days"] = (part - ev).dt.days
    if "process_date" in df.columns:
        out["process_date_mismatch"] = pd.to_datetime(df["process_date"], errors="coerce") != part
    return out


def fk_orphans(tables, foreign_keys):
    """Share of non-null FK values without a matching parent key."""
    out = []
    for child, ccol, parent, pcol in foreign_keys:
        if child not in tables or parent not in tables or ccol not in tables[child].columns:
            continue
        vals = tables[child][ccol].dropna()
        # mentioned-style comma lists are not in FOREIGN_KEYS, so values are single ids
        orphan = ~vals.isin(set(tables[parent][pcol].dropna()))
        out.append({"fk": f"{child}.{ccol} → {parent}.{pcol}", "non_null": len(vals),
                    "orphans": int(orphan.sum()), "orphan_%": round(100 * orphan.mean(), 3) if len(vals) else None,
                    "examples": vals[orphan].head(3).tolist()})
    return pd.DataFrame(out).sort_values("orphan_%", ascending=False)
