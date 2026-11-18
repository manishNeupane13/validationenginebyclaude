"""Fuzzy checks: probable typos in categorical values and near-duplicate names inside one source."""
from __future__ import annotations

from collections import Counter, defaultdict

from rapidfuzz import fuzz, process

from .models import CanonRecord, Issue
from .resolve import keys_conflict
from .schema import CField


def categorical_typos(recs: list[CanonRecord], fields: list[CField], min_support: int = 3, cutoff: int = 78) -> list[Issue]:
    """Flag a rare value that is very close to a frequent one (e.g. 'Frod' vs 'Ford'), with a suggested fix."""
    out = []
    for f in fields:
        if not f.categorical:
            continue
        counts = Counter(r.norm[f.name] for r in recs if r.norm.get(f.name))
        common = [v for v, n in counts.items() if n >= min_support]
        if f.allowed:
            common = list(set(common) | set(f.allowed))
        for r in recs:
            v = r.norm.get(f.name)
            if not v or v in common or counts[v] >= min_support:
                continue
            hit = process.extractOne(v, common, scorer=fuzz.ratio, score_cutoff=cutoff)
            if hit:
                out.append(Issue("spelling", "warning", r.entity, r.source, r.key, f.name, str(r.raw.get(f.name)), hit[0],
                                 f"'{r.raw.get(f.name)}' looks like a misspelling of '{hit[0]}' ({hit[1]:.0f}% similar)", [r.rid],
                                 {"suggestion": hit[0], "similarity": round(hit[1])}))
    return out


def fuzzy_duplicates(recs: list[CanonRecord], fields: list[CField], threshold: int = 90) -> list[Issue]:
    """Near-identical names inside the same source that are not exact duplicates."""
    out, seen_pairs = [], set()
    by_source: dict[str, list[CanonRecord]] = defaultdict(list)
    for r in recs:
        if r.norm.get("full_name"):
            by_source[r.source].append(r)
    for source, rows in by_source.items():
        names = [r.norm["full_name"] for r in rows]
        for i, n in enumerate(names):
            for _m, score, j in process.extract(n, names, scorer=fuzz.token_sort_ratio, score_cutoff=threshold, limit=5):
                if j <= i or names[j] == n:
                    continue
                a, b = rows[i], rows[j]
                if keys_conflict(a, b, fields):
                    continue  # contradicting keys: probably two different people
                if (a.rid, b.rid) in seen_pairs:
                    continue
                seen_pairs.add((a.rid, b.rid))
                for x, y in ((a, b), (b, a)):
                    out.append(Issue("possible_duplicate", "warning", x.entity, source, x.key, "full_name", str(x.raw.get("full_name")),
                                     str(y.raw.get("full_name")),
                                     f"'{x.raw.get('full_name')}' is {score:.0f}% similar to '{y.raw.get('full_name')}' in the same source",
                                     [a.rid, b.rid], {"similarity": round(score)}))
    return out
