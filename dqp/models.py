from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FieldInfo:
    name: str
    dtype: str
    non_null: int
    samples: list[Any]


@dataclass
class CanonRecord:
    """One source row after mapping + normalization."""

    source: str
    entity: str  # canonical entity: driver / vehicle
    key: str  # key inside the source (row number, API id, item id)
    raw: dict[str, Any]  # canonical field -> original value
    norm: dict[str, str | None]  # canonical field -> comparable value
    problems: dict[str, str]  # canonical field -> format problem
    extra: dict[str, Any] = field(default_factory=dict)  # unmapped source columns

    @property
    def rid(self) -> str:
        return f"{self.source}:{self.key}"

    def label(self) -> str:
        for f in ("full_name", "unit_number", "vin"):
            v = self.raw.get(f)
            if v not in (None, ""):
                return str(v)
        return self.rid


@dataclass
class Issue:
    rule: str
    severity: str  # error | warning | info
    entity: str
    source: str  # source name, or "multiple"
    record_key: str  # source row key, or cluster id for cross-source issues
    field: str
    value: str
    expected: str
    message: str
    record_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        raw = "|".join([self.rule, self.entity, self.source, self.record_key, self.field, self.value])
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def to_row(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint, "rule": self.rule, "severity": self.severity,
            "entity": self.entity, "source": self.source, "record_key": self.record_key,
            "field": self.field, "value": self.value, "expected": self.expected,
            "message": self.message, "record_ids": json.dumps(self.record_ids),
            "details": json.dumps(self.details, default=str),
        }
