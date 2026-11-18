from __future__ import annotations

from typing import Any, Iterator

from .base import Connector, register


@register("static")
class StaticConnector(Connector):
    """In-memory rows. Used for the demo and for tests; behaves like any other connector."""

    def __init__(self, name: str, entities: dict[str, list[dict[str, Any]]] | None = None, **extra: Any):
        super().__init__(name, **extra)
        self.entities = entities or {}

    def test_connection(self) -> tuple[bool, str]:
        return True, "Static data source."

    def list_entities(self) -> list[str]:
        return list(self.entities)

    def fetch(self, entity: str, since: str | None = None) -> Iterator[dict[str, Any]]:
        for i, row in enumerate(self.entities[entity]):
            yield {**row, "_key": row.get("_key") or f"s{i + 1}"}
