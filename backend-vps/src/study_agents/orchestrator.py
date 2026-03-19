"""Simple orchestrator CLI to validate config and launch common agents."""
from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path
from typing import Iterable

from .domain_profile_agent import DomainProfileAgent, DomainWizardRequest
from .profile_catalog import ProfileCatalogService
from .profile_cleanup import cleanup_profile_local_artifacts
from .profile_namespace import (
    get_active_profile_file,
    normalize_profile_id,
    read_active_profile,
    write_active_profile,
)
from .settings import SettingsError, get_settings

SERVICE_COMMANDS: dict[str, list[str]] = {
    "cag": ["study-agents-cag"],
    "api": ["study-agents-api"],
    "rag": ["study-agents-rag"],
    "graph": ["study-agents-graph-inspector"],
}


def _print_summary() -> None:
    settings = get_settings()
    for key, value in settings.summary().items():
        print(f"- {key}: {value}")


def _validate(groups: Iterable[str]) -> None:
    settings = get_settings()
    try:
        settings.require_groups(*tuple(groups))
    except SettingsError as exc:
        raise SystemExit(f"❌ validation failed: {exc}") from exc
    settings.ensure_directories()
    print("✅ configuration ok")


def _run_services(names: list[str]) -> None:
    settings = get_settings()
    settings.require_groups("openai", "supabase")

    procs: list[tuple[str, subprocess.Popen]] = []
    try:
        for name in names:
            cmd = SERVICE_COMMANDS[name]
            print(f"▶️  launching {name}: {' '.join(shlex.quote(part) for part in cmd)}")
            proc = subprocess.Popen(cmd)
            procs.append((name, proc))

        print("ℹ️  press Ctrl+C to stop all services.")
        while True:
            alive = [proc.poll() is None for _, proc in procs]
            if not any(alive):
                break
    except KeyboardInterrupt:
        print("\n🛑 stopping services...")
    finally:
        for name, proc in procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            print(f"✅ {name} stopped")


def _profile_list(limit: int, include_inactive: bool) -> None:
    catalog = ProfileCatalogService()
    profiles = catalog.list_profiles(limit=limit, include_inactive=include_inactive)
    active = read_active_profile()
    if not profiles:
        print("No profiles found.")
        return
    for profile in profiles:
        marker = "*" if active and profile["profile_id"] == active else " "
        summary = (profile.get("summary") or "").strip()
        if len(summary) > 80:
            summary = summary[:77] + "..."
        print(
            f"{marker} {profile['profile_id']:<24} docs={profile['doc_count']:<5} "
            f"artifacts={profile['artifact_count']:<5} updated={profile.get('last_activity') or 'n/a'}"
        )
        print(f"    {summary or 'No summary available.'}")


def _profile_show(profile_id: str) -> None:
    catalog = ProfileCatalogService()
    profile = catalog.get_profile(profile_id)
    print(f"Profile: {profile['profile_id']}")
    print(f"Name: {profile.get('name')}")
    print(f"Summary: {profile.get('summary') or 'No summary available.'}")
    print(f"Status: {profile.get('status')}")
    print(f"Prompt Profile: {profile.get('prompt_profile_name') or 'n/a'}")
    print(
        "Counts: "
        f"docs={profile.get('doc_count', 0)} "
        f"nodes={profile.get('node_count', 0)} "
        f"edges={profile.get('edge_count', 0)} "
        f"episodes={profile.get('episode_count', 0)} "
        f"artifacts={profile.get('artifact_count', 0)}"
    )
    print(f"Last Activity: {profile.get('last_activity') or 'n/a'}")
    tags = profile.get("tags") or []
    print(f"Tags: {', '.join(tags) if tags else 'n/a'}")
    recent = profile.get("recent_artifacts") or []
    if recent:
        print("Recent Artifacts:")
        for row in recent[:10]:
            print(
                f"- [{row.get('agent')}] {row.get('artifact_type')} -> {row.get('path')} "
                f"({row.get('created_at')})"
            )


def _profile_use(profile_id: str) -> None:
    catalog = ProfileCatalogService()
    profile = catalog.ensure_profile(
        profile_id,
        name=profile_id,
        tags=["user_created"],
    )
    state_path = write_active_profile(profile["profile_id"])
    print(f"Active profile set to '{profile['profile_id']}'")
    print(f"State file: {state_path}")


def _print_purge_report(report: dict[str, object]) -> None:
    profile_id = str(report.get("profile_id") or "")
    mode = "DRY RUN" if bool(report.get("dry_run")) else "EXECUTED"
    print(f"Purge report ({mode}) for profile: {profile_id}")
    print(
        f"Requested: {report.get('requested_profile_id')} | "
        f"Alias resolved: {bool(report.get('alias_resolved'))} | "
        f"Profile exists: {bool(report.get('profile_exists'))}"
    )

    tables = report.get("tables")
    if isinstance(tables, dict):
        for table_name, raw in tables.items():
            row = raw if isinstance(raw, dict) else {}
            print(
                f"- {table_name}: "
                f"profile_scope={int(row.get('profile_scope_count') or 0)} "
                f"group_scope={int(row.get('group_scope_count') or 0)} "
                f"deleted_profile={int(row.get('deleted_profile_scope') or 0)} "
                f"deleted_group={int(row.get('deleted_group_scope') or 0)} "
                f"remaining_profile={int(row.get('remaining_profile_scope') or 0)} "
                f"remaining_group={int(row.get('remaining_group_scope') or 0)}"
            )

    summary = report.get("summary")
    if isinstance(summary, dict):
        print(
            "Summary: "
            f"candidate_profile_scope={int(summary.get('candidate_rows_by_profile_scope') or 0)} "
            f"candidate_group_scope={int(summary.get('candidate_rows_by_group_scope') or 0)} "
            f"deleted_profile_scope={int(summary.get('deleted_rows_by_profile_scope') or 0)} "
            f"deleted_group_scope={int(summary.get('deleted_rows_by_group_scope') or 0)} "
            f"remaining_profile_scope={int(summary.get('remaining_rows_by_profile_scope') or 0)} "
            f"remaining_group_scope={int(summary.get('remaining_rows_by_group_scope') or 0)}"
        )


def _profile_purge(
    profile_id: str,
    *,
    yes: bool,
    include_artifacts: bool,
    confirm: str | None,
) -> None:
    requested = normalize_profile_id(profile_id)
    expected_confirm = f"PURGE {requested}"
    dry_run = not yes

    if yes and (confirm or "").strip() != expected_confirm:
        raise SystemExit(
            f"--confirm must exactly match '{expected_confirm}' when --yes is supplied."
        )

    catalog = ProfileCatalogService()
    report = catalog.purge_profile_data(
        requested,
        dry_run=dry_run,
        include_artifacts=include_artifacts,
    )
    _print_purge_report(report)
    if dry_run:
        print(
            "Dry run complete. Re-run with "
            f"--yes --confirm '{expected_confirm}' to execute purge."
        )


def _print_delete_report(report: dict[str, object]) -> None:
    db_report = report.get("db_report") if isinstance(report.get("db_report"), dict) else {}
    local_report = report.get("local_report") if isinstance(report.get("local_report"), dict) else {}
    profile_id = str(db_report.get("profile_id") or "")
    mode = "DRY RUN" if bool(db_report.get("dry_run")) else "EXECUTED"
    print(f"Delete profile report ({mode}) for profile: {profile_id}")
    print(
        f"Requested: {db_report.get('requested_profile_id')} | "
        f"Alias resolved: {bool(db_report.get('alias_resolved'))} | "
        f"Profile exists: {bool(db_report.get('profile_exists'))}"
    )

    db_summary = db_report.get("db_summary") if isinstance(db_report.get("db_summary"), dict) else {}
    print(
        "DB summary: "
        f"candidate_rows={int(db_summary.get('candidate_rows') or 0)} "
        f"deleted_rows={int(db_summary.get('deleted_rows') or 0)} "
        f"remaining_rows={int(db_summary.get('remaining_rows') or 0)}"
    )
    purge_report = db_report.get("purge_report") if isinstance(db_report.get("purge_report"), dict) else {}
    purge_summary = purge_report.get("summary") if isinstance(purge_report.get("summary"), dict) else {}
    print(
        "Purge summary: "
        f"candidate_profile_scope={int(purge_summary.get('candidate_rows_by_profile_scope') or 0)} "
        f"candidate_group_scope={int(purge_summary.get('candidate_rows_by_group_scope') or 0)} "
        f"deleted_profile_scope={int(purge_summary.get('deleted_rows_by_profile_scope') or 0)} "
        f"deleted_group_scope={int(purge_summary.get('deleted_rows_by_group_scope') or 0)}"
    )

    local_summary = local_report.get("summary") if isinstance(local_report.get("summary"), dict) else {}
    print(
        "Local cleanup summary: "
        f"candidate_paths={int(local_summary.get('candidate_paths') or 0)} "
        f"existing_paths={int(local_summary.get('existing_paths') or 0)} "
        f"deleted_paths={int(local_summary.get('deleted_paths') or 0)} "
        f"failed_paths={int(local_summary.get('failed_paths') or 0)}"
    )
    if bool(report.get("active_profile_cleared")):
        print("Active profile state file was cleared.")


def _profile_delete(
    profile_id: str,
    *,
    yes: bool,
    confirm: str | None,
) -> None:
    requested = normalize_profile_id(profile_id)
    expected_confirm = f"DELETE PROFILE {requested}"
    dry_run = not yes

    if yes and (confirm or "").strip() != expected_confirm:
        raise SystemExit(
            f"--confirm must exactly match '{expected_confirm}' when --yes is supplied."
        )

    catalog = ProfileCatalogService()
    db_report = catalog.delete_profile_everything(
        requested,
        dry_run=dry_run,
    )
    local_report = cleanup_profile_local_artifacts(
        db_report["profile_id"],
        prompt_profile_name=db_report.get("prompt_profile_name"),
        dry_run=dry_run,
    )
    active_profile_cleared = False
    active_profile = read_active_profile()
    if (
        not dry_run
        and active_profile
        and normalize_profile_id(active_profile) == db_report["profile_id"]
    ):
        state_path: Path = get_active_profile_file()
        state_path.unlink(missing_ok=True)
        active_profile_cleared = True

    report = {
        "db_report": db_report,
        "local_report": local_report,
        "active_profile_cleared": active_profile_cleared,
    }
    _print_delete_report(report)
    if dry_run:
        print(
            "Dry run complete. Re-run with "
            f"--yes --confirm '{expected_confirm}' to execute profile deletion."
        )


def _domain_profile_run(args: argparse.Namespace) -> None:
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
    if result.ok:
        summary = (
            f"Prompt profile managed by domain wizard for {req.domain_seed.strip()}"
            if req.domain_seed and req.domain_seed.strip()
            else f"Prompt profile managed by domain wizard for {req.profile_name}"
        )
        profile = ProfileCatalogService().ensure_profile(
            req.profile_name,
            name=req.profile_name,
            summary=summary,
            prompt_profile_name=req.profile_name,
            tags=["user_created", "domain_wizard"],
        )
        print(f"Catalog profile ensured: {profile['profile_id']}")
    print(f"Exit code: {result.exit_code}")
    print(
        "Command: "
        + " ".join(shlex.quote(part) for part in result.command)
    )
    if result.profile_path:
        print(f"Profile: {result.profile_path}")
    if result.generated_targets:
        print("Generated targets:")
        for target, path in sorted(result.generated_targets.items()):
            print(f"- {target}: {path}")
    if result.prompt_files:
        print("Prompt files:")
        for row in result.prompt_files:
            print(f"- {row.path} ({row.lines} lines, sha256={row.sha256})")
    if result.rolled_back:
        print("Rollback: prompt files restored after failed run.")
    if result.stdout.strip():
        print("Wizard output:")
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print("Wizard stderr:")
        print(result.stderr.rstrip())
    if not result.ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Study Agents orchestrator for validation and service management."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show config summary.")
    status_parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate OpenAI and Supabase credentials before printing status.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate config groups.")
    validate_parser.add_argument(
        "--groups",
        default="openai,supabase,ollama",
        help="Comma-separated config groups to validate.",
    )

    run_parser = subparsers.add_parser("run", help="Launch one or more agents.")
    run_parser.add_argument(
        "--services",
        default="cag,api",
        help=f"Comma-separated services to launch. Choices: {','.join(SERVICE_COMMANDS)}",
    )

    profile_parser = subparsers.add_parser("profile", help="Manage knowledge profiles.")
    profile_parser.add_argument(
        "action",
        choices=["list", "show", "use", "current", "purge", "delete"],
        help="Profile action to run.",
    )
    profile_parser.add_argument("--profile-id", help="Profile ID for show/use/purge/delete actions.")
    profile_parser.add_argument("--limit", type=int, default=100, help="Max list rows.")
    profile_parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include non-active profiles in list output.",
    )
    profile_parser.add_argument(
        "--include-artifacts",
        action="store_true",
        help="Include artifacts table rows in purge.",
    )
    profile_parser.add_argument(
        "--yes",
        action="store_true",
        help="Execute destructive profile purge/delete (otherwise dry-run only).",
    )
    profile_parser.add_argument(
        "--confirm",
        default=None,
        help=(
            "Required with --yes. For purge use 'PURGE <profile-id>'; "
            "for delete use 'DELETE PROFILE <profile-id>'."
        ),
    )

    domain_parser = subparsers.add_parser(
        "domain-profile",
        help="Run the domain prompt profile agent (wrapper over domain_wizard.py).",
    )
    domain_parser.add_argument("--profile-name", required=True, help="Domain prompt profile slug.")
    domain_parser.add_argument("--domain", dest="domain_seed", default=None, help="Optional domain seed.")
    domain_parser.add_argument(
        "--targets",
        default="entity,edge,cag_entity,cag_relationship,vision,cag_answer,scenario_structurer,scenario_context",
        help="Comma-separated wizard targets.",
    )
    domain_parser.add_argument("--env-file", default=".env", help="Env file path.")
    domain_parser.add_argument("--platform", choices=["openai", "ollama"], default=None)
    domain_parser.add_argument("--model", dest="ai_model", default=None)
    domain_parser.add_argument("--ollama-target", choices=["local", "cloud"], default=None)
    domain_parser.add_argument("--temperature", dest="ai_temperature", type=float, default=0.2)
    domain_parser.add_argument("--timeout", dest="timeout_seconds", type=int, default=600)
    domain_parser.add_argument("--no-quickstart", action="store_true")
    domain_parser.add_argument("--no-apply", action="store_true")
    domain_parser.add_argument("--no-check", action="store_true")
    domain_parser.add_argument("--no-ai", action="store_true")
    domain_parser.add_argument("--no-ai-fallback", action="store_true")
    domain_parser.add_argument("--no-rollback", action="store_true")

    args = parser.parse_args()

    if args.command == "status":
        if args.validate:
            _validate(("openai", "supabase"))
        _print_summary()
        return

    if args.command == "validate":
        groups = tuple(grp.strip() for grp in args.groups.split(",") if grp.strip())
        _validate(groups or ("openai", "supabase"))
        return

    if args.command == "run":
        services = [svc.strip() for svc in args.services.split(",") if svc.strip()]
        invalid = [svc for svc in services if svc not in SERVICE_COMMANDS]
        if invalid:
            raise SystemExit(
                f"Unknown services: {', '.join(invalid)}. "
                f"Valid options: {', '.join(SERVICE_COMMANDS)}"
            )
        _run_services(services or ["cag", "api"])
        return

    if args.command == "profile":
        if args.action == "list":
            _profile_list(args.limit, args.include_inactive)
            return
        if args.action == "current":
            print(read_active_profile() or "No active profile set.")
            return
        if not args.profile_id:
            raise SystemExit("--profile-id is required for this action")
        if args.action == "show":
            _profile_show(args.profile_id)
            return
        if args.action == "use":
            _profile_use(args.profile_id)
            return
        if args.action == "purge":
            _profile_purge(
                args.profile_id,
                yes=args.yes,
                include_artifacts=args.include_artifacts,
                confirm=args.confirm,
            )
            return
        if args.action == "delete":
            _profile_delete(
                args.profile_id,
                yes=args.yes,
                confirm=args.confirm,
            )
            return

    if args.command == "domain-profile":
        _domain_profile_run(args)
        return


if __name__ == "__main__":
    main()
