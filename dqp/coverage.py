"""Coverage: which fields, columns and records exist in which platform."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

import pandas as pd

from .models import CanonRecord
from .resolve import Cluster
from .schema import CField


def field_coverage(recs: list[CanonRecord], fields: list[CField]) -> pd.DataFrame:
    """% of rows with a value, per canonical field and platform. NaN = the platform has no such column."""
    sources = sorted({r.source for r in recs})
    data: dict[str, dict[str, float]] = {}
    for f in fields:
        row: dict[str, float] = {}
        for s in sources:
            rows = [r for r in recs if r.source == s]
            if not any(f.name in r.raw for r in rows):
                row[s] = float("nan")
            else:
                row[s] = round(100 * sum(r.norm.get(f.name) is not None for r in rows) / len(rows), 1)
        data[f.label] = row
    return pd.DataFrame(data).T[sources] if sources else pd.DataFrame()


def record_counts(recs: list[CanonRecord]) -> pd.DataFrame:
    c = Counter(r.source for r in recs)
    return pd.DataFrame({"Platform": list(c), "Records": list(c.values())})


def entity_presence(clusters: list[Cluster], sources: list[str]) -> pd.DataFrame:
    """How many real-world records appear in each combination of platforms."""
    combos = Counter(tuple(sorted(cl.sources)) for cl in clusters)
    rows = []
    for combo, n in combos.most_common():
        rows.append({"Platforms": " + ".join(combo), "Records": n, **{s: (s in combo) for s in sources}})
    return pd.DataFrame(rows)


def presence_table(clusters: list[Cluster], sources: list[str]) -> pd.DataFrame:
    """One row per real-world record with a yes/no per platform (used for 'is in' / 'not in' filters)."""
    rows = []
    for cl in clusters:
        rows.append({"Cluster": cl.id, "Record": cl.label(), "Platforms": len(cl.sources),
                     **{s: (s in cl.sources) for s in sources}})
    return pd.DataFrame(rows)


def column_stats(recs: list[CanonRecord], fields: list[CField], source: str) -> pd.DataFrame:
    rows = [r for r in recs if r.source == source]
    out = []
    for f in fields:
        if not any(f.name in r.raw for r in rows):
            continue
        vals = [r.raw.get(f.name) for r in rows if r.norm.get(f.name) is not None]
        top = Counter(str(v) for v in vals).most_common(3)
        out.append({"Field": f.label, "Filled": len(vals), "Empty": len(rows) - len(vals),
                    "Filled %": round(100 * len(vals) / len(rows), 1) if rows else 0.0,
                    "Distinct": len({r.norm[f.name] for r in rows if r.norm.get(f.name)}),
                    "Most common": ", ".join(f"{v} ({n})" for v, n in top)})
    return pd.DataFrame(out)


def column_matrix(mappings: list[dict[str, Any]], canonical_entity: str, fields: list[CField]) -> pd.DataFrame:
    """Canonical field -> the source column that feeds it, per platform."""
    ms = [m for m in mappings if m["canonical_entity"] == canonical_entity]
    sources = sorted({m["source"] for m in ms})
    table: dict[str, dict[str, str]] = defaultdict(dict)
    for m in ms:
        for col, fname in m["columns"].items():
            table[fname][m["source"]] = col
    rows = [{"Field": f.label, **{s: table.get(f.name, {}).get(s, "—") for s in sources}} for f in fields]
    return pd.DataFrame(rows)
