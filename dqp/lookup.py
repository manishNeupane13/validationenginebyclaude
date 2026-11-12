"""'Is it in?' lookups and search across platforms."""
from __future__ import annotations

import pandas as pd
from rapidfuzz import fuzz, process

from .models import CanonRecord
from .normalize import name_key, normalize
from .schema import CField


def is_in(values: list[str], recs: list[CanonRecord], fields: list[CField]) -> pd.DataFrame:
    """For each pasted value (name, VIN, license, ID, email, phone...) show which platforms contain it.

    Matching is on normalized values, so '(312) 555-0142' finds '+1 312-555-0142' and 'Smith, Jo' finds 'Jo Smith'.
    Values with no exact match get the closest name (>= 85%) as a 'similar' hint instead of a silent miss.
    """
    sources = sorted({r.source for r in recs})
    searchable = [f for f in fields if f.key or f.dtype in ("name", "phone", "email")]
    index: dict[tuple[str, str], list[CanonRecord]] = {}
    for r in recs:
        for f in searchable:
            v = r.norm.get(f.name)
            if v:
                index.setdefault((f.name, v), []).append(r)
    names = {r.norm["full_name"]: r for r in recs if r.norm.get("full_name")}
    rows = []
    for raw in values:
        raw = raw.strip()
        if not raw:
            continue
        hits: list[tuple[CField, CanonRecord]] = []
        for f in searchable:
            n = normalize(f, raw).norm
            for r in index.get((f.name, n or ""), []):
                hits.append((f, r))
        found = {r.source for _, r in hits}
        matched_as = ", ".join(sorted({f.label for f, _ in hits}))
        record = hits[0][1].label() if hits else ""
        note = ""
        if not hits and names:
            best = process.extractOne(name_key(raw), list(names), scorer=fuzz.token_sort_ratio, score_cutoff=85)
            if best:
                r = names[best[0]]
                found, record, matched_as = {x.source for x in recs if x.norm.get("full_name") == best[0]}, r.label(), "Driver name (similar)"
                note = f"No exact match; closest is '{r.label()}' ({best[1]:.0f}% similar)"
        rows.append({"Searched for": raw, "Found in": f"{len(found)} of {len(sources)}", "Matched as": matched_as,
                     "Record": record, **{s: (s in found) for s in sources}, "Note": note})
    return pd.DataFrame(rows)


def search_rows(frames: dict[tuple[str, str], pd.DataFrame], query: str, limit: int = 500) -> pd.DataFrame:
    """Case-insensitive substring search over every column of every platform's latest data."""
    q = query.strip().lower()
    out = []
    if not q:
        return pd.DataFrame()
    for (source, entity), df in frames.items():
        if df.empty:
            continue
        txt = df.astype(str)
        mask = txt.apply(lambda c: c.str.lower().str.contains(q, regex=False)).any(axis=1)
        for _, row in df[mask].head(limit).iterrows():
            hit_cols = [c for c in df.columns if c != "_key" and q in str(row[c]).lower()]
            out.append({"Platform": source, "Entity": entity, "Row": row["_key"],
                        "Matched in": ", ".join(f"{c}: {row[c]}" for c in hit_cols[:3]),
                        "Preview": " | ".join(str(row[c]) for c in df.columns[1:5] if str(row[c]) not in ("None", "nan"))})
    return pd.DataFrame(out)
