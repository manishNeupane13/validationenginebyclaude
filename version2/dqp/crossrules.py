"""Cross-platform rules on resolved clusters: missing in a platform, mismatched values, missing values."""
from __future__ import annotations

from collections import defaultdict

from rapidfuzz import fuzz

from .models import Issue
from .resolve import Cluster
from .schema import CField


def cross_rules(entity: str, clusters: list[Cluster], fields: list[CField], expected: list[str]) -> list[Issue]:
    out: list[Issue] = []
    for cl in clusters:
        rids = [m.rid for m in cl.members]
        label = cl.label()
        for s in sorted(set(expected) - cl.sources):
            out.append(Issue("missing_in_platform", "warning", entity, s, cl.id, "", label, "record present",
                             f"{label} exists in {', '.join(sorted(cl.sources))} but not in {s}", rids,
                             {"present_in": sorted(cl.sources), "missing_from": s}))
        if len(cl.sources) < 2 and len(cl.members) < 2:
            continue
        for f in fields:
            per_src: dict[str, list[str]] = defaultdict(list)
            norm_by_src: dict[str, set[str]] = defaultdict(set)
            for m in cl.members:
                raw, nv = m.raw.get(f.name), m.norm.get(f.name)
                if f.name in m.raw:
                    norm_by_src.setdefault(m.source, set())
                if nv:
                    per_src[m.source].append(str(raw))
                    norm_by_src[m.source].add(nv)
            distinct = set().union(*norm_by_src.values()) if norm_by_src else set()
            if len(distinct) > 1:
                shown = {s: " / ".join(sorted(set(v))) for s, v in per_src.items()}
                sev = "error" if f.key else "warning"
                msg = f"{f.label} differs: " + "; ".join(f"{s} = {v}" for s, v in shown.items())
                vals = sorted(distinct)
                if f.dtype in ("name", "text") and len(vals) == 2:
                    sim = fuzz.ratio(vals[0], vals[1])
                    if sim >= 75:
                        msg += f" (likely spelling difference, {sim:.0f}% similar)"
                out.append(Issue("mismatch", sev, entity, "multiple", cl.id, f.name,
                                 " | ".join(f"{s}={v}" for s, v in shown.items()), "same value everywhere", msg, rids,
                                 {"by_source": shown, "label": label}))
            elif len(distinct) == 1:
                for s, vs in norm_by_src.items():
                    if not vs and s in cl.sources:
                        out.append(Issue("value_missing", "info", entity, s, cl.id, f.name, "", next(iter(distinct)),
                                         f"{f.label} is empty in {s} but filled in other platforms", rids,
                                         {"known_value": next(iter(distinct)), "label": label}))
    return out
