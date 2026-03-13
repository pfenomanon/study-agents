"""Shared Supabase client helpers with reusable HTTP configuration."""
from __future__ import annotations

import atexit
import os
from functools import lru_cache
from typing import Optional

import httpx
from supabase import Client, ClientOptions, create_client

from .settings import SettingsError, get_settings

__all__ = ["get_supabase_client", "create_supabase_client"]


_HTTPX_CLIENT: Optional[httpx.Client] = None


def _normalize_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _http_timeout_seconds() -> float:
    raw = os.getenv("SUPABASE_HTTP_TIMEOUT", "30")
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 30.0


def _get_httpx_client() -> httpx.Client:
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is None:
        timeout = httpx.Timeout(_http_timeout_seconds())
        verify = _normalize_bool(os.getenv("SUPABASE_HTTP_VERIFY"), True)
        _HTTPX_CLIENT = httpx.Client(timeout=timeout, verify=verify)
        atexit.register(_HTTPX_CLIENT.close)
    return _HTTPX_CLIENT


def _build_client(url: str | None, key: str | None) -> Client:
    if not url or not key:
        raise SettingsError("Supabase URL/key must be configured before use.")
    options = ClientOptions(httpx_client=_get_httpx_client())
    return create_client(url, key, options=options)


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """Get a memoized Supabase client using configured env vars."""
    settings = get_settings()
    settings.require_groups("supabase")
    return _build_client(settings.supabase_url, settings.supabase_key)


def create_supabase_client(url: str | None = None, key: str | None = None) -> Client:
    """
    Build a Supabase client with shared HTTP configuration.

    Args:
        url: Optional override for the Supabase URL. Falls back to settings.
        key: Optional override for the Supabase key. Falls back to settings.
    """
    if url and key:
        return _build_client(url, key)
    settings = get_settings()
    settings.require_groups("supabase")
    return _build_client(url or settings.supabase_url, key or settings.supabase_key)
