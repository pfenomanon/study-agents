"""Shared Ollama client that injects cloud host and API key headers."""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional

import ollama

from .config import OLLAMA_API_KEY, OLLAMA_HOST


class OllamaConfigError(RuntimeError):
    """Raised when required Ollama settings are missing."""


@lru_cache(maxsize=1)
def _client() -> ollama.Client:
    """Memoized Ollama client configured for cloud usage."""
    if not OLLAMA_HOST:
        raise OllamaConfigError("OLLAMA_HOST is not configured")

    headers: dict[str, str] = {}
    if OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"

    return ollama.Client(host=OLLAMA_HOST, headers=headers or None)


def chat(
    *,
    model: str,
    messages: Iterable[Mapping[str, Any]],
    stream: bool = False,
    **kwargs: Any,
) -> Any:
    """Call Ollama chat with configured host/api key."""
    client = _client()
    return client.chat(model=model, messages=messages, stream=stream, **kwargs)
