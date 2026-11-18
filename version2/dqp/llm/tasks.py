"""What we ask the model to do. It only advises: every answer is validated, grounded in the evidence, and shown to a person."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..models import CanonRecord
from ..schema import ENTITIES, field_map
from ..store import Store, now
from .client import LLMClient

PROMPT_VERSION = "1"
SENSITIVE_FREEFORM = {"license", "vin", "phone", "email", "id", "plate"}  # the model may never invent these


@dataclass
class Opinion:
    verdict: str
    confidence: float
    reason: str
    extra: dict[str, Any] = field(default_factory=dict)


UNUSABLE = Opinion("unsure", 0.0, "The model did not return a usable answer.")


def mask(value: Any, dtype: str) -> str:
    v = "" if value is None else str(value)
    if dtype == "email" and "@" in v:
        a, d = v.split("@", 1)
        return f"{a[:1]}***@{d}"
    if dtype == "phone":
        digits = re.sub(r"\D", "", v)
        return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"
    return v


def record_view(r: CanonRecord, hide_personal: bool) -> dict[str, str]:
    fm = field_map(r.entity)
    out = {"platform": r.source}
    for name, raw in r.raw.items():
        if raw not in (None, "") and r.norm.get(name) is not None:
            out[fm[name].label] = mask(raw, fm[name].dtype) if hide_personal else str(raw)
    return out


def _ask(llm: LLMClient, store: Store | None, system: str, user: str, check) -> Opinion:
    key = hashlib.sha1(f"{llm.model}|{PROMPT_VERSION}|{system}|{user}".encode()).hexdigest()
    if store:
        with store.conn() as c:
            hit = c.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        if hit:
            return Opinion(**json.loads(hit["response"]))
    last = UNUSABLE
    for attempt in range(2):
        try:
            raw = llm.complete_json(system, user if attempt == 0 else user + "\n\nYour previous reply was invalid. Follow the JSON format exactly.")
            last = check(raw)
            break
        except Exception:  # noqa: BLE001  bad JSON, wrong keys, HTTP errors on the second try are reported as unsure
            continue
    if store and last is not UNUSABLE:
        with store.conn() as c:
            c.execute("INSERT OR REPLACE INTO llm_cache VALUES(?,?,?)", (key, json.dumps(last.__dict__), now()))
    return last


def _conf(raw: dict) -> float:
    return min(max(float(raw.get("confidence", 0)), 0.0), 1.0)


# ---- 1. do two records describe the same real-world thing? ------------------------------------
MATCH_SYSTEM = """You compare two records from different fleet-management systems and decide whether they describe the same {entity}.
Use only the data shown. Expect typos, 'Last, First' order, nickname or spelling differences, and missing fields.
Two different license numbers or VINs are strong evidence of different {entity}s, unless they differ by a single obvious typo (a swapped or wrong character) and everything else agrees.
Do not guess. If the evidence is thin or conflicting, answer "unsure".
Reply with JSON only: {{"verdict": "same" | "different" | "unsure", "confidence": number 0 to 1, "reason": "one or two plain sentences"}}"""


def judge_match(llm: LLMClient, a: CanonRecord, b: CanonRecord, store: Store | None = None, hide_personal: bool = False) -> Opinion:
    def check(raw: dict) -> Opinion:
        v = str(raw.get("verdict", "")).lower().strip()
        if v not in {"same", "different", "unsure"}:
            raise ValueError(v)
        return Opinion(v, _conf(raw), str(raw.get("reason", ""))[:500])

    user = json.dumps({"record_A": record_view(a, hide_personal), "record_B": record_view(b, hide_personal)}, indent=1)
    return _ask(llm, store, MATCH_SYSTEM.format(entity=a.entity), user, check)


# ---- 2. second opinion on a flagged issue, with a grounded suggested fix ------------------------
ISSUE_SYSTEM = """You review data-quality findings for a fleet company. An automated rule flagged the issue below.
Decide: "real_problem", "false_alarm" (the data is actually fine, e.g. a harmless formatting difference or a legitimately different value), or "unsure".
If it is a real problem and the evidence shows the correct value (other platforms agree, or the value is an obvious typo of an allowed or expected value), give it as suggested_value and say which platform holds the correct one in correct_source.
Never invent license numbers, VINs, phone numbers, emails or IDs that do not appear in the evidence: use null.
Reply with JSON only: {"verdict": "real_problem" | "false_alarm" | "unsure", "confidence": number 0 to 1, "suggested_value": string or null, "correct_source": string or null, "reason": "one or two plain sentences"}"""


def issue_context(issue: dict, records: list[CanonRecord], hide_personal: bool) -> dict[str, Any]:
    details = json.loads(issue.get("details") or "{}")
    fm = field_map(issue["entity"]) if issue["entity"] in ENTITIES else {}
    f = fm.get(issue["field"])
    ctx: dict[str, Any] = {"rule": issue["rule"], "field": f.label if f else issue["field"], "message": issue["message"],
                           "value_found": issue["value"], "expected": issue["expected"], "values_by_platform": details.get("by_source"),
                           "records": [record_view(r, hide_personal) for r in records[:4]]}
    if f and f.allowed:
        ctx["allowed_values"] = list(f.allowed)
    if details.get("suggestion"):
        ctx["rule_suggestion"] = details["suggestion"]
    return {k: v for k, v in ctx.items() if v not in (None, "", [], {})}


def review_issue(llm: LLMClient, issue: dict, records: list[CanonRecord], store: Store | None = None, hide_personal: bool = False) -> Opinion:
    ctx = issue_context(issue, records, hide_personal)
    evidence = json.dumps(ctx).lower()
    f = field_map(issue["entity"]).get(issue["field"]) if issue["entity"] in ENTITIES else None

    def check(raw: dict) -> Opinion:
        v = str(raw.get("verdict", "")).lower().strip().replace(" ", "_")
        if v not in {"real_problem", "false_alarm", "unsure"}:
            raise ValueError(v)
        extra: dict[str, Any] = {}
        sv = raw.get("suggested_value")
        reason = str(raw.get("reason", ""))[:500]
        if sv not in (None, "", "null"):
            sv = str(sv).strip()
            # grounding: identifiers must already appear in the evidence, otherwise the model invented them
            if f and f.dtype in SENSITIVE_FREEFORM and sv.lower() not in evidence:
                reason += " (A suggested value was dropped because it does not appear in the data.)"
            else:
                extra["suggested_value"] = sv
                if raw.get("correct_source"):
                    extra["correct_source"] = str(raw["correct_source"])
        return Opinion(v, _conf(raw), reason, extra)

    return _ask(llm, store, ISSUE_SYSTEM, json.dumps(ctx, indent=1), check)


# ---- 3. plain-language report ---------------------------------------------------------------------
REPORT_SYSTEM = """You write a short data-quality briefing for an operations manager, from the numbers provided.
Use only those numbers and examples; do not invent counts. Max 220 words, plain language, markdown with three short sections:
**What looks healthy**, **Biggest problems**, **What to do first**.
Reply with JSON only: {"report": "markdown text"}"""


def write_report(llm: LLMClient, facts: dict[str, Any]) -> str:
    raw = llm.complete_json(REPORT_SYSTEM, json.dumps(facts, indent=1, default=str))
    text = str(raw.get("report", "")).strip()
    if not text:
        raise ValueError("The model returned an empty report.")
    return text
