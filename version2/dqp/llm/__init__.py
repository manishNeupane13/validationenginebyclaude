from __future__ import annotations

from ..store import Store
from .client import LLMClient, MockLLM, OpenAICompatClient  # noqa: F401
from .review import ReviewQueue  # noqa: F401


def make_client(store: Store, api_key: str | None = None) -> OpenAICompatClient:
    """Build the model client from saved settings. Defaults to a local Ollama server."""
    return OpenAICompatClient(store.get_setting("llm_base_url", "http://localhost:11434/v1"),
                              store.get_setting("llm_model", "llama3.1:8b"), api_key)


def llm_options(store: Store) -> dict:
    return {"hide_personal": store.get_setting("llm_mask", "") == "1",
            "auto_threshold": float(store.get_setting("llm_auto", "0") or 0)}
