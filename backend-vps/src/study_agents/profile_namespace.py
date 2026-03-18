from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_PROFILE_RE = re.compile(r"[^a-z0-9_-]+")


def normalize_profile_id(value: str | None) -> str:
    """Normalize free-form profile input into a stable slug."""
    raw = (value or "").strip().lower()
    if not raw:
        raise ValueError("profile_id cannot be empty")
    cleaned = _PROFILE_RE.sub("-", raw)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        raise ValueError("profile_id resolved to empty after normalization")
    return cleaned[:64]


def safe_doc_slug(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return f"doc-{uuid.uuid4().hex[:8]}"
    cleaned = _PROFILE_RE.sub("-", raw)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:96] or f"doc-{uuid.uuid4().hex[:8]}"


def compose_group_id(profile_id: str, agent_name: str, doc_slug: str | None = None) -> str:
    profile = normalize_profile_id(profile_id)
    agent = safe_doc_slug(agent_name)
    if doc_slug:
        return f"profile:{profile}:{agent}:{safe_doc_slug(doc_slug)}"
    return f"profile:{profile}:{agent}"


def infer_profile_id_from_group_id(group_id: str | None) -> str | None:
    if not group_id:
        return None
    raw = group_id.strip()
    if not raw:
        return None
    if raw.startswith("profile:"):
        parts = raw.split(":")
        if len(parts) >= 2 and parts[1].strip():
            return normalize_profile_id(parts[1])
    token = raw.split(":", 1)[0].strip()
    if not token:
        return None
    return normalize_profile_id(token)


@dataclass(slots=True)
class ActiveProfileState:
    profile_id: str


_DEFAULT_ACTIVE_PROFILE_FILE = Path(
    os.getenv("STUDY_AGENTS_ACTIVE_PROFILE_FILE", "data/profiles/active_profile.json")
).expanduser().resolve()


def get_active_profile_file() -> Path:
    return _DEFAULT_ACTIVE_PROFILE_FILE


def read_active_profile() -> str | None:
    env_override = (os.getenv("STUDY_AGENTS_ACTIVE_PROFILE") or "").strip()
    if env_override:
        return normalize_profile_id(env_override)

    path = get_active_profile_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    profile = str(data.get("profile_id") or "").strip()
    if not profile:
        return None
    try:
        return normalize_profile_id(profile)
    except ValueError:
        return None


def write_active_profile(profile_id: str) -> Path:
    normalized = normalize_profile_id(profile_id)
    path = get_active_profile_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"profile_id": normalized}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_profile_id(
    explicit_profile: str | None = None,
    *,
    default_profile: str = "default",
    allow_default: bool = True,
) -> str | None:
    if explicit_profile and explicit_profile.strip():
        return normalize_profile_id(explicit_profile)

    active = read_active_profile()
    if active:
        return active

    env_default = (os.getenv("STUDY_AGENTS_DEFAULT_PROFILE") or "").strip()
    if env_default:
        return normalize_profile_id(env_default)

    if allow_default:
        return normalize_profile_id(default_profile)
    return None


def build_profile_output_dir(
    root: Path,
    profile_id: str,
    agent_name: str,
    run_id: str | None = None,
) -> Path:
    profile = normalize_profile_id(profile_id)
    agent = safe_doc_slug(agent_name)
    base = root / "profiles" / profile / agent
    return base / safe_doc_slug(run_id) if run_id else base
