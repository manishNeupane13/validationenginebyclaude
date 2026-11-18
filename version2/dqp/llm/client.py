"""LLM access. Anything that speaks the OpenAI chat-completions protocol works:
Ollama (http://localhost:11434/v1), LM Studio, vLLM, llama.cpp server, or a hosted API."""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class LLMClient(Protocol):
    model: str

    def complete_json(self, system: str, user: str) -> dict[str, Any]: ...


def parse_json(text: str) -> dict[str, Any]:
    """Models often wrap JSON in prose or code fences; take the first {...} block."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        out = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise ValueError("no JSON object in model reply")
        out = json.loads(m.group(0))
    if not isinstance(out, dict):
        raise ValueError("model reply is not a JSON object")
    return out


class OpenAICompatClient:
    def __init__(self, base_url: str = "http://localhost:11434/v1", model: str = "llama3.1:8b", api_key: str | None = None,
                 timeout: float = 180, transport: httpx.BaseTransport | None = None):
        self.base_url, self.model, self.api_key, self.timeout, self._transport = base_url.rstrip("/"), model, api_key, timeout, transport

    @property
    def is_local(self) -> bool:
        return (urlparse(self.base_url).hostname or "") in LOCAL_HOSTS

    def _post(self, messages: list[dict[str, str]]) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        with httpx.Client(timeout=self.timeout, transport=self._transport, headers=headers) as c:
            r = c.post(f"{self.base_url}/chat/completions", json={
                "model": self.model, "messages": messages, "temperature": 0, "response_format": {"type": "json_object"}})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            return parse_json(self._post(msgs))
        except ValueError:  # one retry for malformed JSON
            msgs.append({"role": "user", "content": "Reply with one valid JSON object only. No other text."})
            return parse_json(self._post(msgs))

    def test(self) -> tuple[bool, str]:
        try:
            self.complete_json("Reply with JSON.", 'Return {"ok": true}')
            where = "on this machine" if self.is_local else "at a REMOTE address (data leaves this machine)"
            return True, f"Model '{self.model}' answered, running {where}."
        except Exception as e:  # noqa: BLE001
            return False, f"Could not reach the model: {e}"


class MockLLM:
    """Scripted model for tests and demos: pass a function (system, user) -> dict."""

    def __init__(self, fn: Callable[[str, str], dict[str, Any]], model: str = "mock"):
        self.fn, self.model, self.calls = fn, model, []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.calls.append((system, user))
        return self.fn(system, user)
