"""Identity resolution: decide which rows in different platforms describe the same driver / vehicle.

Cascade (strongest first):
  1. a shared strong key (license number, VIN, employee id, unit number, plate, email)
  2. the same normalized name
  3. a very similar name (>= LINK_SCORE) when keys do not contradict each other and the
     two groups do not already contain a row from the same platform
Pairs scoring between REVIEW_SCORE and LINK_SCORE are returned as candidates for a human.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from rapidfuzz import fuzz, process

from .models import CanonRecord
from .schema import CField

LINK_SCORE = 92
REVIEW_SCORE = 85
KEY_CONFLICT = 80  # two keys that are less similar than this contradict each other


@dataclass
class Cluster:
    id: str
    entity: str
    members: list[CanonRecord]
    confidence: float
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def sources(self) -> set[str]:
        return {m.source for m in self.members}

    def label(self) -> str:
        return self.members[0].label()


def keys_conflict(a: CanonRecord, b: CanonRecord, fields: list[CField]) -> bool:
    for f in fields:
        if f.key:
            x, y = a.norm.get(f.name), b.norm.get(f.name)
            if x and y and x != y and fuzz.ratio(x, y) < KEY_CONFLICT:
                return True
    return False


def names_differ(a: CanonRecord, b: CanonRecord, fields: list[CField], floor: int = 60) -> bool:
    if not any(f.name == "full_name" for f in fields):
        return False
    x, y = a.norm.get("full_name"), b.norm.get("full_name")
    return bool(x and y and fuzz.token_sort_ratio(x, y) < floor)


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def resolve(entity: str, recs: list[CanonRecord], fields: list[CField]) -> tuple[list[Cluster], list[dict]]:
    n = len(recs)
    uf = _UF(n)
    links: list[tuple[int, int, str]] = []

    # 1. strong keys
    for f in fields:
        if not f.key:
            continue
        idx: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(recs):
            v = r.norm.get(f.name)
            if v:
                idx[v].append(i)
        for v, ids in idx.items():
            for pos, j in enumerate(ids[1:], start=1):
                # a shared key with clearly different names is a key collision (reported as a duplicate), not one person
                mate = next((i for i in ids[:pos] if not names_differ(recs[i], recs[j], fields)), None)
                if mate is not None:
                    uf.union(mate, j)
                    links.append((mate, j, f"same {f.label.lower()}"))

    candidates: list[dict] = []
    name_field = "full_name" if any(f.name == "full_name" for f in fields) else None
    if name_field:
        # 2. exact normalized name
        idx = defaultdict(list)
        for i, r in enumerate(recs):
            if r.norm.get(name_field):
                idx[r.norm[name_field]].append(i)
        for ids in idx.values():
            for j in ids[1:]:
                for i in ids:
                    if i != j and uf.find(i) != uf.find(j) and not keys_conflict(recs[i], recs[j], fields):
                        uf.union(i, j)
                        links.append((i, j, "same name"))
                        break
        # 3. fuzzy name
        named = [i for i, r in enumerate(recs) if r.norm.get(name_field)]
        if len(named) > 1:
            names = [recs[i].norm[name_field] for i in named]
            sims = process.cdist(names, names, scorer=fuzz.token_sort_ratio, score_cutoff=REVIEW_SCORE, dtype=np.uint8, workers=-1)
            pairs = sorted(((int(sims[a, b]), a, b) for a, b in np.argwhere(np.triu(sims, 1) > 0)), reverse=True)
            for score, a, b in pairs:
                i, j = named[a], named[b]
                if uf.find(i) == uf.find(j) or keys_conflict(recs[i], recs[j], fields):
                    continue
                if score >= LINK_SCORE:
                    src_i = {recs[k].source for k in range(n) if uf.find(k) == uf.find(i)}
                    src_j = {recs[k].source for k in range(n) if uf.find(k) == uf.find(j)}
                    if not (src_i & src_j):
                        uf.union(i, j)
                        links.append((i, j, f"similar name ({score}%)"))
                        continue
                candidates.append({"entity": entity, "a": recs[i].rid, "b": recs[j].rid, "score": score,
                                   "a_label": recs[i].label(), "b_label": recs[j].label()})

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    clusters: list[Cluster] = []
    prefix = entity[0].upper()
    for k, (root, ids) in enumerate(sorted(groups.items(), key=lambda kv: min(kv[1])), start=1):
        reasons: dict[str, str] = {}
        why: dict[int, list[str]] = defaultdict(list)
        score = 1.0
        for a, b, why_text in links:
            if uf.find(a) == root:
                why[a].append(why_text)
                why[b].append(why_text)
                if why_text.startswith("similar"):
                    score = min(score, int(why_text.split("(")[1].split("%")[0]) / 100)
                elif why_text == "same name":
                    score = min(score, 0.9)
        for i in ids:
            reasons[recs[i].rid] = ", ".join(sorted(set(why[i]))) or ("single record" if len(ids) == 1 else "")
        clusters.append(Cluster(f"{prefix}{k:04d}", entity, [recs[i] for i in ids], score if len(ids) > 1 else 1.0, reasons))
    return clusters, candidates
