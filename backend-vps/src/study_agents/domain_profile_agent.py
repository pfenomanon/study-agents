"""Control-plane agent wrapper around scripts/domain_wizard.py."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shlex
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .profile_catalog import ProfileCatalogService
from .profile_namespace import normalize_profile_id


ROOT = Path(__file__).resolve().parents[2]
DOMAIN_WIZARD_PATH = ROOT / "scripts" / "domain_wizard.py"
PROMPTS_DIR = ROOT / "prompts"

TARGET_OUTPUTS: dict[str, str] = {
    "entity": "kg_entity_extraction.txt",
    "edge": "kg_edge_extraction.txt",
    "cag_entity": "cag_entity_extraction.txt",
    "cag_relationship": "cag_relationship_extraction.txt",
    "vision": "vision_reasoning.txt",
    "cag_answer": "cag_answer_generation.txt",
    "scenario_structurer": "scenario_structuring_system.txt",
    "scenario_context": "scenario_question_context_template.txt",
}

_DEFAULT_TARGETS = ",".join(TARGET_OUTPUTS.keys())
_GENERATED_PROMPT_RE = re.compile(r"^Generated\s+(\w+)\s+prompt:\s+(.+)$")
_PROFILE_PATH_RE = re.compile(r"^Created profile via quickstart:\s+(.+)$")


@dataclass(slots=True)
class DomainWizardRequest:
    profile_name: str
    domain_seed: str | None = None
    quickstart: bool = True
    apply: bool = True
    check: bool = True
    use_ai: bool = True
    targets: str = _DEFAULT_TARGETS
    env_file: str | None = ".env"
    platform: str | None = None
    ai_model: str | None = None
    ollama_target: str | None = None
    ai_temperature: float = 0.2
    no_ai_fallback: bool = False
    timeout_seconds: int = 600
    rollback_on_error: bool = True


@dataclass(slots=True)
class PromptFileSummary:
    path: str
    lines: int
    sha256: str


@dataclass(slots=True)
class DomainWizardResult:
    ok: bool
    exit_code: int
    command: list[str]
    profile_path: str | None
    generated_targets: dict[str, str]
    prompt_files: list[PromptFileSummary]
    rolled_back: bool
    stdout: str
    stderr: str


class DomainProfileAgent:
    """Executes the domain wizard in a controlled, auditable manner."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.root = root or ROOT
        self.domain_wizard_path = self.root / "scripts" / "domain_wizard.py"
        self.prompts_dir = self.root / "prompts"
        self.python_executable = python_executable or sys.executable

    def build_command(self, req: DomainWizardRequest) -> list[str]:
        cmd = [
            self.python_executable,
            str(self.domain_wizard_path),
            "--profile-name",
            req.profile_name,
            "--targets",
            req.targets,
            "--temperature",
            str(req.ai_temperature),
        ]
        if req.quickstart:
            cmd.append("--quickstart")
        if req.domain_seed:
            cmd.extend(["--domain", req.domain_seed])
        if req.apply:
            cmd.append("--apply")
        if req.check:
            cmd.append("--check")
        if req.use_ai:
            cmd.append("--use-ai")
        if req.env_file:
            cmd.extend(["--env-file", req.env_file])
        if req.platform:
            cmd.extend(["--platform", req.platform])
        if req.ai_model:
            cmd.extend(["--model", req.ai_model])
        if req.ollama_target:
            cmd.extend(["--ollama-target", req.ollama_target])
        if req.no_ai_fallback:
            cmd.append("--no-ai-fallback")
        return cmd

    def run(self, req: DomainWizardRequest) -> DomainWizardResult:
        if not self.domain_wizard_path.exists():
            raise RuntimeError(f"Domain wizard script not found: {self.domain_wizard_path}")
        if not req.profile_name.strip():
            raise ValueError("profile_name is required")

        cmd = self.build_command(req)
        snapshots = self._snapshot_target_files(req.targets) if req.apply else {}
        proc = subprocess.run(
            cmd,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=max(1, req.timeout_seconds),
            env=dict(os.environ),
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        profile_path, generated_targets = self._parse_output(stdout)

        rolled_back = False
        if proc.returncode != 0 and req.apply and req.rollback_on_error:
            self._restore_target_files(snapshots)
            rolled_back = True

        prompt_files = self._summarize_prompt_files(req.targets)
        return DomainWizardResult(
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            command=cmd,
            profile_path=profile_path,
            generated_targets=generated_targets,
            prompt_files=prompt_files,
            rolled_back=rolled_back,
            stdout=stdout,
            stderr=stderr,
        )

    def _parse_output(self, stdout: str) -> tuple[str | None, dict[str, str]]:
        profile_path: str | None = None
        generated_targets: dict[str, str] = {}
        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            prompt_match = _GENERATED_PROMPT_RE.match(line)
            if prompt_match:
                target = prompt_match.group(1).strip()
                path = prompt_match.group(2).strip()
                generated_targets[target] = path
                continue
            profile_match = _PROFILE_PATH_RE.match(line)
            if profile_match:
                profile_path = profile_match.group(1).strip()
        return profile_path, generated_targets

    def _resolve_target_paths(self, targets: str) -> list[Path]:
        requested = [part.strip() for part in targets.split(",") if part.strip()]
        paths: list[Path] = []
        for target in requested:
            filename = TARGET_OUTPUTS.get(target)
            if filename:
                paths.append(self.prompts_dir / filename)
        return paths

    def _snapshot_target_files(self, targets: str) -> dict[Path, bytes | None]:
        snapshots: dict[Path, bytes | None] = {}
        for path in self._resolve_target_paths(targets):
            snapshots[path] = path.read_bytes() if path.exists() else None
        return snapshots

    def _restore_target_files(self, snapshots: dict[Path, bytes | None]) -> None:
        for path, content in snapshots.items():
            if content is None:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    def _summarize_prompt_files(self, targets: str) -> list[PromptFileSummary]:
        summaries: list[PromptFileSummary] = []
        for path in self._resolve_target_paths(targets):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            summaries.append(
                PromptFileSummary(
                    path=str(path),
                    lines=text.count("\n") + (1 if text else 0),
                    sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
        return summaries


def _print_result(result: DomainWizardResult) -> None:
    print(f"ok: {result.ok}")
    print(f"exit_code: {result.exit_code}")
    print("command: " + " ".join(shlex.quote(part) for part in result.command))
    if result.profile_path:
        print(f"profile_path: {result.profile_path}")
    if result.generated_targets:
        print("generated_targets:")
        for target, path in sorted(result.generated_targets.items()):
            print(f"  - {target}: {path}")
    if result.prompt_files:
        print("prompt_files:")
        for row in result.prompt_files:
            print(f"  - {row.path} ({row.lines} lines, sha256={row.sha256})")
    if result.rolled_back:
        print("rollback: restored previous prompt files after failure")
    if result.stdout.strip():
        print("stdout:")
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print("stderr:")
        print(result.stderr.rstrip())


def _sync_profile_catalog(req: DomainWizardRequest, result: DomainWizardResult) -> None:
    if not result.ok:
        return

    profile_name = normalize_profile_id(req.profile_name)
    summary = (
        f"Prompt profile managed by domain wizard for {req.domain_seed.strip()}"
        if req.domain_seed and req.domain_seed.strip()
        else f"Prompt profile managed by domain wizard for {profile_name}"
    )
    prompt_rows = [
        {"path": row.path, "lines": row.lines, "sha256": row.sha256}
        for row in result.prompt_files
    ]
    metadata = {
        "exit_code": result.exit_code,
        "generated_targets": result.generated_targets,
        "prompt_files": prompt_rows,
        "rolled_back": result.rolled_back,
        "profile_path": result.profile_path,
    }

    catalog = ProfileCatalogService()
    profile = catalog.ensure_profile(
        profile_name,
        name=profile_name,
        summary=summary,
        prompt_profile_name=profile_name,
        tags=["user_created", "domain_wizard"],
    )
    artifact_path = result.profile_path or f"domain/profiles/{profile_name}.json"
    catalog.record_artifact(
        profile_id=profile["profile_id"],
        agent="domain_profile_agent",
        artifact_type="prompt_profile_bundle",
        path=artifact_path,
        run_id=uuid.uuid4().hex[:8],
        source_ids=[profile_name],
        metadata=metadata,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Control-plane agent wrapper for scripts/domain_wizard.py."
    )
    parser.add_argument("--profile-name", required=True, help="Domain prompt profile slug.")
    parser.add_argument("--domain", dest="domain_seed", default=None)
    parser.add_argument("--targets", default=_DEFAULT_TARGETS)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--platform", choices=["openai", "ollama"], default=None)
    parser.add_argument("--model", dest="ai_model", default=None)
    parser.add_argument("--ollama-target", choices=["local", "cloud"], default=None)
    parser.add_argument("--temperature", dest="ai_temperature", type=float, default=0.2)
    parser.add_argument("--timeout", dest="timeout_seconds", type=int, default=600)
    parser.add_argument("--no-quickstart", action="store_true")
    parser.add_argument("--no-apply", action="store_true")
    parser.add_argument("--no-check", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--no-ai-fallback", action="store_true")
    parser.add_argument("--no-rollback", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    req = DomainWizardRequest(
        profile_name=args.profile_name,
        domain_seed=args.domain_seed,
        quickstart=not args.no_quickstart,
        apply=not args.no_apply,
        check=not args.no_check,
        use_ai=not args.no_ai,
        targets=args.targets,
        env_file=args.env_file,
        platform=args.platform,
        ai_model=args.ai_model,
        ollama_target=args.ollama_target,
        ai_temperature=args.ai_temperature,
        no_ai_fallback=args.no_ai_fallback,
        timeout_seconds=args.timeout_seconds,
        rollback_on_error=not args.no_rollback,
    )
    result = DomainProfileAgent().run(req)
    try:
        _sync_profile_catalog(req, result)
    except Exception as exc:  # noqa: BLE001
        print(f"[catalog warn] Failed to sync profile catalog: {exc}", file=sys.stderr)
    _print_result(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
