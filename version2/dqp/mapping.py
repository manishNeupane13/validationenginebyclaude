"""Suggest source column -> canonical field mappings, with a reason for each suggestion."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz, process

from .normalize import EMAIL_RE, VIN_RE, is_blank
from .schema import CField, fields_of


@dataclass
class Suggestion:
    source_column: str
    field: str
    score: float
    reason: str


def _clean(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _content_hint(values: list[Any]) -> tuple[str, str] | None:
    vals = [str(v).strip() for v in values if not is_blank(v)][:50]
    if len(vals) < 3:
        return None
    share = lambda fn: sum(fn(v) for v in vals) / len(vals)  # noqa: E731
    if share(lambda v: bool(VIN_RE.match(re.sub(r"[^A-Za-z0-9]", "", v).upper()))) >= 0.8:
        return "vin", "most values look like 17-character VINs"
    if share(lambda v: bool(EMAIL_RE.match(v))) >= 0.8:
        return "email", "most values look like email addresses"
    return None


def suggest(columns: list[str], entity: str, samples: dict[str, list[Any]] | None = None) -> list[Suggestion]:
    samples = samples or {}
    flds: list[CField] = fields_of(entity)
    cands: list[Suggestion] = []
    for col in columns:
        if col.startswith("_"):
            continue
        c = _clean(col.split(".")[-1] if "." in col and not col.lower().startswith("static") else col)
        c_full = _clean(col)
        hint = _content_hint(samples.get(col, []))
        for f in flds:
            syns = {_clean(s) for s in f.synonyms} | {_clean(f.name), _clean(f.label)}
            if c_full in syns or c in syns:
                cands.append(Suggestion(col, f.name, 100, "matches a known name for this field"))
                continue
            best = process.extractOne(c_full, syns, scorer=fuzz.WRatio)
            if best and best[1] >= 82:
                cands.append(Suggestion(col, f.name, float(best[1]), f"similar to '{best[0]}'"))
            if hint and hint[0] == f.name:
                cands.append(Suggestion(col, f.name, 90, hint[1]))
    # one column per field and one field per column, best score first
    cands.sort(key=lambda s: -s.score)
    used_c, used_f, out = set(), set(), []
    for s in cands:
        if s.source_column in used_c or s.field in used_f:
            continue
        used_c.add(s.source_column)
        used_f.add(s.field)
        out.append(s)
    return out
