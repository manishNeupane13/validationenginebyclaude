from __future__ import annotations

from typing import Any, Iterator

import httpx

from .base import Connector, register

URL = "https://api.monday.com/v2"
ITEM_FIELDS = "id name column_values { id text }"
FIRST = f"""query($ids:[ID!], $limit:Int){{ boards(ids:$ids){{ columns{{ id title }}
  items_page(limit:$limit){{ cursor items{{ {ITEM_FIELDS} }} }} }} }}"""
NEXT = f"""query($cursor:String!, $limit:Int){{ next_items_page(cursor:$cursor, limit:$limit){{
  cursor items{{ {ITEM_FIELDS} }} }} }}"""


@register("monday")
class MondayConnector(Connector):
    """Monday.com GraphQL API. `boards` maps an entity label to a board id, e.g. {"new_drivers": 123456}."""

    def __init__(self, name: str, boards: dict[str, int | str] | None = None, token: str | None = None,
                 token_env: str = "MONDAY_API_TOKEN", page_size: int = 100,
                 transport: httpx.BaseTransport | None = None, **extra: Any):
        super().__init__(name, boards=boards or {}, token=token, token_env=token_env, **extra)
        self.boards, self.page_size, self._transport = boards or {}, page_size, transport

    def _post(self, client: httpx.Client, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        r = client.post(URL, json={"query": query, "variables": variables})
        r.raise_for_status()
        body = r.json()
        if body.get("errors"):
            raise RuntimeError(f"Monday API error: {body['errors'][0].get('message')}")
        return body["data"]

    def _client(self) -> httpx.Client:
        token = self.secret()
        if not token:
            raise RuntimeError("Monday token missing: set the env var named in token_env or paste a token.")
        return httpx.Client(timeout=30, transport=self._transport,
                            headers={"Authorization": token, "API-Version": "2024-10"})

    def test_connection(self) -> tuple[bool, str]:
        try:
            with self._client() as c:
                self._post(c, "query { me { id } }", {})
            return True, "Connected to Monday."
        except Exception as e:  # noqa: BLE001
            return False, f"Monday connection failed: {e}"

    def list_entities(self) -> list[str]:
        return list(self.boards)

    def fetch(self, entity: str, since: str | None = None) -> Iterator[dict[str, Any]]:
        if entity not in self.boards:
            raise ValueError(f"Board '{entity}' is not configured. Add it under boards.")
        with self._client() as c:
            data = self._post(c, FIRST, {"ids": [str(self.boards[entity])], "limit": self.page_size})
            board = data["boards"][0]
            titles = {col["id"]: col["title"] for col in board["columns"]}
            page = board["items_page"]
            seen_cursors: set[str] = set()
            while True:
                for item in page["items"]:
                    row: dict[str, Any] = {"_key": str(item["id"]), "Name": item["name"]}
                    for cv in item["column_values"]:
                        row[titles.get(cv["id"], cv["id"])] = cv.get("text") or None
                    yield row
                cursor = page.get("cursor")
                if not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
                page = self._post(c, NEXT, {"cursor": cursor, "limit": self.page_size})["next_items_page"]
