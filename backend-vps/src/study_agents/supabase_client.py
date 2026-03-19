"""Shared Supabase client helpers with reusable HTTP configuration."""
from __future__ import annotations

import atexit
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import httpx

from .settings import SettingsError, get_settings

__all__ = ["get_supabase_client", "create_supabase_client"]

if TYPE_CHECKING:
    from supabase import Client as SupabaseClient
else:  # pragma: no cover - runtime typing fallback
    SupabaseClient = Any


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


@lru_cache(maxsize=1)
def _load_supabase_symbols():
    """
    Import Supabase SDK symbols while tolerating local folder shadowing.

    Running from this repository root introduces a top-level `supabase/` folder
    used by the Supabase CLI. That directory can shadow the installed Python
    package and break `from supabase import ...`. If we detect that shadowing,
    retry import without the repo root on sys.path.
    """
    try:
        from supabase import Client, ClientOptions, create_client
        return Client, ClientOptions, create_client
    except Exception:
        loaded = sys.modules.get("supabase")
        shadowed = loaded is not None and not hasattr(loaded, "create_client")
        if not shadowed:
            raise

        repo_root = Path(__file__).resolve().parents[2]
        original_path = list(sys.path)
        try:
            sys.modules.pop("supabase", None)
            sys.path = [
                entry
                for entry in sys.path
                if Path(entry or ".").resolve() != repo_root
            ]
            try:
                from supabase import Client, ClientOptions, create_client
                return Client, ClientOptions, create_client
            except ModuleNotFoundError:
                # Host Python may not have deps installed while project .venv does.
                venv_site_packages = _project_venv_site_packages(repo_root)
                if venv_site_packages is None:
                    raise
                sys.path.insert(0, str(venv_site_packages))
                original_path = [str(venv_site_packages), *original_path]
                from supabase import Client, ClientOptions, create_client
                return Client, ClientOptions, create_client
        finally:
            sys.path = original_path


def _project_venv_site_packages(repo_root: Path) -> Path | None:
    lib_dir = repo_root / ".venv" / "lib"
    if not lib_dir.exists():
        return None
    for py_dir in sorted(lib_dir.glob("python*"), reverse=True):
        site_packages = py_dir / "site-packages"
        if site_packages.exists():
            return site_packages
    return None


def _build_client(url: str | None, key: str | None) -> SupabaseClient:
    if not url or not key:
        raise SettingsError("Supabase URL/key must be configured before use.")
    _, client_options_cls, create_client_fn = _load_supabase_symbols()
    options = client_options_cls(httpx_client=_get_httpx_client())
    return create_client_fn(url, key, options=options)


@lru_cache(maxsize=1)
def get_supabase_client() -> SupabaseClient:
    """Get a memoized Supabase client using configured env vars."""
    settings = get_settings()
    settings.require_groups("supabase")
    return _build_client(settings.supabase_url, settings.supabase_key)


def create_supabase_client(
    url: str | None = None, key: str | None = None
) -> SupabaseClient:
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
