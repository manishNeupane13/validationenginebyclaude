"""Single-source rules: missing, format, enum, exact duplicates."""
from __future__ import annotations

from collections import defaultdict

from .models import CanonRecord, Issue
from .schema import CField


def missing_required(recs: list[CanonRecord], fields: list[CField]) -> list[Issue]:
    out = []
    for r in recs:
        for f in fields:
            if f.required and r.norm.get(f.name) is None and f.name in r.raw:
                shown = r.raw.get(f.name)
                out.append(Issue("missing", "error", r.entity, r.source, r.key, f.name, "" if shown is None else str(shown),
                                 "a value", f"{f.label} is empty", [r.rid]))
    return out


def format_problems(recs: list[CanonRecord], fields: list[CField]) -> list[Issue]:
    fm = {f.name: f for f in fields}
    out = []
    for r in recs:
        for name, msg in r.problems.items():
            f = fm[name]
            sev = "error" if f.key or f.required else "warning"
            rule = "invalid_value" if f.dtype == "enum" else "format"
            out.append(Issue(rule, sev, r.entity, r.source, r.key, name, str(r.raw.get(name)), f.dtype, f"{f.label}: {msg}", [r.rid]))
    return out


def exact_duplicates(recs: list[CanonRecord], fields: list[CField]) -> list[Issue]:
    out = []
    by_source: dict[str, list[CanonRecord]] = defaultdict(list)
    for r in recs:
        by_source[r.source].append(r)
    for source, rows in by_source.items():
        for f in fields:
            if not f.key:
                continue
            seen: dict[str, list[CanonRecord]] = defaultdict(list)
            for r in rows:
                v = r.norm.get(f.name)
                if v:
                    seen[v].append(r)
            for v, group in seen.items():
                if len(group) > 1:
                    ids = [g.rid for g in group]
                    for g in group:
                        out.append(Issue("duplicate", "error", g.entity, source, g.key, f.name, str(g.raw.get(f.name)), "unique value",
                                         f"{f.label} '{g.raw.get(f.name)}' appears {len(group)} times in {source}", ids,
                                         {"rows": [x.key for x in group]}))
    return out


def run_single_source(recs: list[CanonRecord], fields: list[CField]) -> list[Issue]:
    return missing_required(recs, fields) + format_problems(recs, fields) + exact_duplicates(recs, fields)
