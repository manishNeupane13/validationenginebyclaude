"""Build canonical records from raw snapshots using saved mappings."""
from __future__ import annotations

from typing import Any

import pandas as pd

from .models import CanonRecord
from .normalize import normalize
from .schema import ENTITIES, field_map
from .store import Store


def build_records(source: str, canonical_entity: str, df: pd.DataFrame, columns: dict[str, str]) -> list[CanonRecord]:
    fmap = field_map(canonical_entity)
    out: list[CanonRecord] = []
    for row in df.to_dict("records"):
        raw: dict[str, Any] = {}
        norm: dict[str, str | None] = {}
        problems: dict[str, str] = {}
        for col, fname in columns.items():
            if fname not in fmap or col not in row:
                continue
            v = row[col]
            if isinstance(v, float) and v != v:
                v = None
            res = normalize(fmap[fname], v)
            raw[fname], norm[fname] = v, res.norm
            if not res.ok:
                problems[fname] = res.msg
        extra = {k: v for k, v in row.items() if k not in columns and not k.startswith("_")}
        out.append(CanonRecord(source, canonical_entity, str(row["_key"]), raw, norm, problems, extra))
    return out


def build_all(store: Store) -> dict[str, list[CanonRecord]]:
    result: dict[str, list[CanonRecord]] = {e: [] for e in ENTITIES}
    for m in store.mappings():
        df = store.rows(m["source"], m["source_entity"])
        if df.empty or m["canonical_entity"] not in result:
            continue
        result[m["canonical_entity"]] += build_records(m["source"], m["canonical_entity"], df, m["columns"])
    return {k: v for k, v in result.items() if v}
