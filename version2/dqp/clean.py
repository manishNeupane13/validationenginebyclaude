"""Cleaning step between fetch and store: keep only the columns you need, filter rows, tidy values.

Nothing here looks at meaning. Columns that are mapped (or explicitly kept) survive; the rest are never saved.
Only column NAMES and counts of dropped columns are remembered, never their values.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .normalize import PLACEHOLDERS

FILTER_OPS = ["is in", "is not in", "contains", "is empty", "has a value"]


@dataclass
class CleanConfig:
    keep_all: bool = False                         # True = do not drop any column
    extra_keep: list[str] = field(default_factory=list)   # kept even though they are not mapped
    filters: list[dict[str, Any]] = field(default_factory=list)  # "keep only rows where": {column, op, values}
    trim: bool = True                              # strip spaces
    blanks_to_null: bool = True                    # "", "N/A", "null", "-" ... become empty
    drop_empty_rows: bool = True
    drop_duplicate_rows: bool = False
    keep_raw: bool = False                         # also archive the untouched fetch for a few days

    @classmethod
    def load(cls, data: dict[str, Any] | None) -> "CleanConfig":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def dump(self) -> dict[str, Any]:
        return asdict(self)


def clean_value(v: Any, cfg: CleanConfig) -> Any:
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, str):
        if cfg.trim:
            v = re.sub(r"\s+", " ", v.replace("\u00a0", " ")).strip()
        if cfg.blanks_to_null and v.strip().lower() in PLACEHOLDERS:
            return None
    elif v is None:
        return None
    return v


def _passes(full: dict[str, Any], flt: dict[str, Any]) -> bool:
    val = full.get(flt["column"])
    text = "" if val is None else str(val).strip().lower()
    wanted = [str(x).strip().lower() for x in flt.get("values", []) if str(x).strip()]
    op = flt["op"]
    if op == "is empty":
        return text == ""
    if op == "has a value":
        return text != ""
    if not wanted:
        return True  # an unfinished filter must not silently delete everything
    if op == "is in":
        return text in wanted
    if op == "is not in":
        return text not in wanted
    return any(w in text for w in wanted)  # contains


def apply(rows: list[dict[str, Any]], cfg: CleanConfig, mapped_cols: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cols: list[str] = []
    for r in rows:
        for c in r:
            if c != "_key" and c not in cols:
                cols.append(c)
    wanted = set(mapped_cols) | set(cfg.extra_keep)
    keep = cols if cfg.keep_all else [c for c in cols if c in wanted]
    unknown = [f["column"] for f in cfg.filters if f["column"] not in cols]
    filters = [f for f in cfg.filters if f["column"] in cols]
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    dropped_fill = {c: 0 for c in cols if c not in keep}
    stats = {"rows_filtered": 0, "rows_empty": 0, "rows_duplicate": 0}
    for row in rows:
        full = {c: clean_value(row.get(c), cfg) for c in cols}
        for c in dropped_fill:
            dropped_fill[c] += full[c] is not None
        if not all(_passes(full, f) for f in filters):
            stats["rows_filtered"] += 1
            continue
        kept = {c: full[c] for c in keep}
        if cfg.drop_empty_rows and all(v is None for v in kept.values()):
            stats["rows_empty"] += 1
            continue
        if cfg.drop_duplicate_rows:
            sig = tuple(kept.values())
            if sig in seen:
                stats["rows_duplicate"] += 1
                continue
            seen.add(sig)
        out.append({"_key": row["_key"], **kept})
    meta = {"fetched_rows": len(rows), "fetched_columns": cols, "kept_columns": keep, "kept_rows": len(out),
            "dropped_columns": dropped_fill, "unknown_filter_columns": unknown, **stats}
    return out, meta
