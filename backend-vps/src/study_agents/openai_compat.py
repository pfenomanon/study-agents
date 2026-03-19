"""Compatibility helpers for OpenAI chat completion calls."""
from __future__ import annotations

from typing import Any


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    text = str(exc).lower()
    if "temperature" not in text:
        return False
    markers = (
        "unsupported parameter",
        "unsupported_parameter",
        "unsupported_value",
        "does not support",
        "only the default",
        "param': 'temperature'",
        '"param": "temperature"',
    )
    return any(marker in text for marker in markers)


def create_chat_completion(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    **kwargs: Any,
) -> Any:
    """Create a chat completion, retrying without temperature when unsupported."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        **kwargs,
    }
    if temperature is not None:
        payload["temperature"] = temperature

    try:
        return client.chat.completions.create(**payload)
    except Exception as exc:
        if temperature is not None and _is_unsupported_temperature_error(exc):
            payload.pop("temperature", None)
            return client.chat.completions.create(**payload)
        raise
