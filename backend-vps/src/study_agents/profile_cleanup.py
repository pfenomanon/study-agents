from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .profile_namespace import normalize_profile_id


def _is_within_root(target: Path, root: Path) -> bool:
    try:
        target_resolved = target.resolve()
        root_resolved = root.resolve()
    except Exception:
        return False
    return target_resolved == root_resolved or root_resolved in target_resolved.parents


def _allowed_roots() -> list[Path]:
    cwd = Path.cwd().resolve()
    return [
        Path("/app/data"),
        Path("/app/research_output"),
        Path("/tmp/study_agents/research_output"),
        Path("/app/domain/profiles"),
        Path("/app/domain/research"),
        (cwd / "data"),
        (cwd / "research_output"),
        (cwd / "domain/profiles"),
        (cwd / "domain/research"),
    ]


def _is_allowed_path(path: Path) -> bool:
    return any(_is_within_root(path, root) for root in _allowed_roots())


def _file_tree_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return 1, int(path.stat().st_size)
        except Exception:
            return 1, 0

    files = 0
    bytes_total = 0
    try:
        for row in path.rglob("*"):
            if row.is_file():
                files += 1
                try:
                    bytes_total += int(row.stat().st_size)
                except Exception:
                    pass
    except Exception:
        pass
    return files, bytes_total


def _prompt_candidates(profile_id: str, prompt_profile_name: str | None) -> list[str]:
    names = {normalize_profile_id(profile_id)}
    if prompt_profile_name:
        try:
            names.add(normalize_profile_id(prompt_profile_name))
        except ValueError:
            pass
    return sorted(names)


def _profile_candidates(profile_id: str, prompt_profile_name: str | None) -> list[Path]:
    profile = normalize_profile_id(profile_id)
    prompt_names = _prompt_candidates(profile, prompt_profile_name)
    cwd = Path.cwd().resolve()

    base_candidates = [
        Path("/app/data/output/research/profiles") / profile,
        Path("/app/research_output/profiles") / profile,
        Path("/tmp/study_agents/research_output/profiles") / profile,
        (cwd / "data/output/research/profiles" / profile),
        (cwd / "research_output/profiles" / profile),
        (cwd / "data/profiles" / profile),
    ]

    prompt_paths: list[Path] = []
    prompt_roots = [
        Path("/app/domain/profiles"),
        Path("/app/domain/research"),
        (cwd / "domain/profiles"),
        (cwd / "domain/research"),
    ]
    for name in prompt_names:
        profile_root, research_root, profile_root_local, research_root_local = prompt_roots
        prompt_paths.extend(
            [
                profile_root / f"{name}.json",
                research_root / name,
                profile_root_local / f"{name}.json",
                research_root_local / name,
            ]
        )
        for root in (profile_root, profile_root_local):
            if root.exists():
                prompt_paths.extend(root.glob(f"{name}.json.bak.*"))
                prompt_paths.extend(root.glob(f"{name}.json.corrupt.*"))

    deduped: list[Path] = []
    seen: set[str] = set()
    for item in [*base_candidates, *prompt_paths]:
        key = str(item.resolve()) if item.exists() else str(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def cleanup_profile_local_artifacts(
    profile_id: str,
    *,
    prompt_profile_name: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    profile = normalize_profile_id(profile_id)
    candidates = _profile_candidates(profile, prompt_profile_name)

    rows: list[dict[str, Any]] = []
    for path in candidates:
        exists = path.exists()
        is_file = path.is_file() if exists else False
        is_dir = path.is_dir() if exists else False
        file_count, bytes_total = _file_tree_stats(path) if exists else (0, 0)
        allowed = _is_allowed_path(path)
        row = {
            "path": str(path),
            "exists": exists,
            "kind": "file" if is_file else "dir" if is_dir else "missing",
            "file_count": file_count,
            "bytes": bytes_total,
            "allowed": allowed,
            "deleted": False,
            "error": None,
        }
        if exists and not allowed:
            row["error"] = "path_not_allowed"
        rows.append(row)

    if not dry_run:
        for row in rows:
            if not row["exists"] or not row["allowed"]:
                continue
            path = Path(str(row["path"]))
            try:
                if row["kind"] == "file":
                    path.unlink(missing_ok=True)
                elif row["kind"] == "dir":
                    shutil.rmtree(path, ignore_errors=False)
                row["deleted"] = True
            except Exception as exc:  # noqa: BLE001
                row["error"] = str(exc)
            row["exists"] = path.exists()

    summary = {
        "candidate_paths": len(rows),
        "existing_paths": sum(1 for row in rows if bool(row.get("exists"))),
        "existing_files": sum(int(row.get("file_count") or 0) for row in rows),
        "existing_bytes": sum(int(row.get("bytes") or 0) for row in rows),
        "deleted_paths": sum(1 for row in rows if bool(row.get("deleted"))),
        "failed_paths": sum(1 for row in rows if row.get("error")),
    }

    return {
        "profile_id": profile,
        "prompt_candidates": _prompt_candidates(profile, prompt_profile_name),
        "dry_run": dry_run,
        "paths": rows,
        "summary": summary,
    }
