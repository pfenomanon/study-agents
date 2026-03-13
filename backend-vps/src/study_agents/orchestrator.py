"""Simple orchestrator CLI to validate config and launch common agents."""
from __future__ import annotations

import argparse
import shlex
import subprocess
from typing import Iterable

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
    # Always ensure OpenAI + Supabase are configured for long-running services.
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

    args = parser.parse_args()

    if args.command == "status":
        if args.validate:
            _validate(("openai", "supabase"))
        _print_summary()
        return

    if args.command == "validate":
        groups = tuple(
            grp.strip() for grp in args.groups.split(",") if grp.strip()
        )
        _validate(groups or ("openai", "supabase"))
        return

    if args.command == "run":
        services = [
            svc.strip() for svc in args.services.split(",") if svc.strip()
        ]
        invalid = [svc for svc in services if svc not in SERVICE_COMMANDS]
        if invalid:
            raise SystemExit(
                f"Unknown services: {', '.join(invalid)}. "
                f"Valid options: {', '.join(SERVICE_COMMANDS)}"
            )
        _run_services(services or ["cag", "api"])
        return


if __name__ == "__main__":
    main()
