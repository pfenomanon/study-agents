#!/usr/bin/env python3
"""Builds the study-agents project zip plus the bootstrap bundle zip."""

from __future__ import annotations

import os
import shutil
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TIMESTAMP = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
PROJECT_ZIP = DIST / f"study-agents-{TIMESTAMP}.zip"
BUNDLE_ZIP = DIST / f"bootstrap-package-{TIMESTAMP}.zip"

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}
EXCLUDE_PATH_PREFIXES = {
    Path("docker/authelia"),
    Path("docker/internal-tls"),
    Path("docker/vault/bootstrap"),
    Path("docker/vault/data"),
    Path("data"),
    Path("knowledge_graph"),
    Path("research_output"),
    Path("temp_images"),
    Path("supabase/.temp"),
}


def has_prefix(path: Path, prefix: Path) -> bool:
    return path == prefix or prefix in path.parents


def should_skip_path(rel_path: Path) -> bool:
    return any(has_prefix(rel_path, prefix) for prefix in EXCLUDE_PATH_PREFIXES)


def should_skip_dir(rel_path: Path) -> bool:
    if should_skip_path(rel_path):
        return True
    return any(part in EXCLUDE_DIRS for part in rel_path.parts if part not in (".", ""))


def should_skip_file(rel_path: Path) -> bool:
    if rel_path.name == ".env":
        return True
    if should_skip_path(rel_path):
        return True
    if rel_path.name in EXCLUDE_FILES:
        return True
    return any(part in EXCLUDE_DIRS for part in rel_path.parts if part not in (".", ""))


def clean_dist() -> None:
    if DIST.exists():
        for entry in DIST.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    else:
        DIST.mkdir(parents=True, exist_ok=True)


def iter_project_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)

        pruned = []
        for dirname in dirnames:
            rel = rel_dir / dirname
            if should_skip_dir(rel):
                continue
            pruned.append(dirname)
        dirnames[:] = pruned

        for filename in filenames:
            rel_path = (rel_dir / filename) if str(rel_dir) != "." else Path(filename)
            if should_skip_file(rel_path):
                continue
            yield ROOT / rel_path, rel_path


def build_project_zip() -> None:
    with zipfile.ZipFile(PROJECT_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path, rel_path in iter_project_files():
            arcname = Path("study-agents") / rel_path
            zf.write(abs_path, arcname)


def normalized_text(path: Path) -> bytes:
    data = path.read_text(encoding="utf-8")
    return data.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def add_script(zipf: zipfile.ZipFile, source: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname)
    mtime = datetime.fromtimestamp(int(source.stat().st_mtime), timezone.utc)
    info.date_time = mtime.timetuple()[:6]
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    zipf.writestr(info, normalized_text(source))


def add_regular_file(zipf: zipfile.ZipFile, source: Path, arcname: str) -> None:
    zipf.write(source, arcname)


def build_bootstrap_bundle() -> None:
    with zipfile.ZipFile(BUNDLE_ZIP, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        add_script(zf, ROOT / "scripts" / "bootstrap.sh", "bootstrap.sh")
        add_script(zf, ROOT / "scripts" / "setup_local_supabase.sh", "setup_local_supabase.sh")
        add_regular_file(zf, PROJECT_ZIP, PROJECT_ZIP.name)
        for doc_name in ("README.md", "DEPLOYMENT.md", "README_BACKEND_VPS_QUICKSTART.md"):
            doc_path = ROOT / doc_name
            if doc_path.exists():
                add_regular_file(zf, doc_path, doc_name)


def main() -> None:
    clean_dist()
    DIST.mkdir(parents=True, exist_ok=True)
    build_project_zip()
    build_bootstrap_bundle()
    print(f"Wrote project archive: {PROJECT_ZIP}")
    print(f"Wrote bootstrap bundle: {BUNDLE_ZIP}")


if __name__ == "__main__":
    main()
