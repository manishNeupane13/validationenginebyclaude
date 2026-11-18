from __future__ import annotations

import time
from typing import Any, Iterator

import httpx

from .base import Connector, flatten, register

# Endpoint assumptions live here only. Verify against your Samsara API version.
ENTITIES = {
    "vehicles": "/fleet/vehicles",
    "drivers": "/fleet/drivers",
}
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


@register("samsara")
class SamsaraConnector(Connector):
    """Samsara REST API. Cursor pagination: pagination.hasNextPage / pagination.endCursor."""

    def __init__(self, name: str, base_url: str = "https://api.samsara.com", token: str | None = None,
                 token_env: str = "SAMSARA_API_TOKEN", page_size: int = 512, max_retries: int = 4,
                 transport: httpx.BaseTransport | None = None, **extra: Any):
        super().__init__(name, base_url=base_url, token=token, token_env=token_env, **extra)
        self.base_url, self.page_size, self.max_retries = base_url.rstrip("/"), page_size, max_retries
        self._transport = transport

    def _client(self) -> httpx.Client:
        token = self.secret()
        if not token:
            raise RuntimeError("Samsara token missing: set the env var named in token_env or paste a token.")
        return httpx.Client(base_url=self.base_url, timeout=30, transport=self._transport,
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})

    def _get(self, client: httpx.Client, path: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                r = client.get(path, params=params)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
                time.sleep(min(2 ** attempt * 0.5, 8))
                continue
            if r.status_code in RETRY_STATUS and attempt < self.max_retries:
                wait = float(r.headers.get("Retry-After", min(2 ** attempt * 0.5, 8)))
                time.sleep(min(wait, 30))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError("unreachable")

    def test_connection(self) -> tuple[bool, str]:
        try:
            with self._client() as c:
                self._get(c, ENTITIES["vehicles"], {"limit": 1})
            return True, "Connected to Samsara."
        except Exception as e:  # noqa: BLE001
            return False, f"Samsara connection failed: {e}"

    def list_entities(self) -> list[str]:
        return list(ENTITIES)

    def fetch(self, entity: str, since: str | None = None) -> Iterator[dict[str, Any]]:
        if entity not in ENTITIES:
            raise ValueError(f"Unknown Samsara entity '{entity}'")
        seen_cursors: set[str] = set()
        seen_ids: set[str] = set()
        cursor: str | None = None
        with self._client() as c:
            while True:
                params: dict[str, Any] = {"limit": self.page_size}
                if cursor:
                    params["after"] = cursor
                body = self._get(c, ENTITIES[entity], params)
                for item in body.get("data", []):
                    rid = str(item.get("id", ""))
                    if rid in seen_ids:
                        continue
                    seen_ids.add(rid)
                    row = flatten(item)
                    row["_key"] = rid
                    yield row
                page = body.get("pagination") or {}
                cursor = page.get("endCursor")
                if not page.get("hasNextPage") or not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
