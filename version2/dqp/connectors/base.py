from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Iterator

from ..models import FieldInfo

_REGISTRY: dict[str, type["Connector"]] = {}


def register(kind: str):
    def deco(cls):
        cls.kind = kind
        _REGISTRY[kind] = cls
        return cls
    return deco


def build_connector(kind: str, name: str, config: dict[str, Any]) -> "Connector":
    if kind not in _REGISTRY:
        raise ValueError(f"Unknown connector '{kind}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[kind](name, **config)


def available_kinds() -> list[str]:
    return sorted(_REGISTRY)


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested JSON to dotted keys so every API row is one flat dict."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = None
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            out[prefix] = ", ".join(str(x) for x in obj)
        else:
            out[prefix] = json.dumps(obj)
    else:
        out[prefix] = obj
    return out


class Connector(ABC):
    """Contract every platform implements. Connectors only fetch raw data; they never validate."""

    kind: str = "base"

    def __init__(self, name: str, **config: Any):
        self.name = name
        self.config = config

    def secret(self, key: str = "token") -> str | None:
        """Token from config (session only) or from the env var named in `<key>_env`."""
        return self.config.get(key) or os.environ.get(self.config.get(f"{key}_env", ""), None)

    @abstractmethod
    def test_connection(self) -> tuple[bool, str]: ...

    @abstractmethod
    def list_entities(self) -> list[str]: ...

    @abstractmethod
    def fetch(self, entity: str, since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield flat dict rows. Every row must include `_key` (stable id inside the source)."""

    def schema(self, entity: str, sample: int = 200) -> list[FieldInfo]:
        rows = []
        for i, row in enumerate(self.fetch(entity)):
            rows.append(row)
            if i + 1 >= sample:
                break
        cols: dict[str, list] = {}
        for r in rows:
            for k, v in r.items():
                if k != "_key":
                    cols.setdefault(k, []).append(v)
        return [
            FieldInfo(c, type(next((x for x in vs if x is not None), "")).__name__,
                      sum(v not in (None, "") for v in vs), [v for v in vs if v not in (None, "")][:3])
            for c, vs in cols.items()
        ]
