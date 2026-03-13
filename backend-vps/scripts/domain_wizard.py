#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "domain" / "profiles"
PROMPTS_DIR = ROOT / "prompts"
TEMPLATES_DIR = PROMPTS_DIR / "templates"
DEFAULT_OUTPUT_PATH = PROMPTS_DIR / "kg_entity_extraction.txt"

TARGETS = {
    "entity": {
        "template": TEMPLATES_DIR / "kg_entity_extraction.base.txt",
        "output": PROMPTS_DIR / "kg_entity_extraction.txt",
    },
    "edge": {
        "template": TEMPLATES_DIR / "kg_edge_extraction.base.txt",
        "output": PROMPTS_DIR / "kg_edge_extraction.txt",
    },
    "vision": {
        "template": TEMPLATES_DIR / "vision_reasoning.base.txt",
        "output": PROMPTS_DIR / "vision_reasoning.txt",
    },
}

REQUIRED_PROFILE_KEYS = {
    "schema_version",
    "profile_name",
    "domain_name",
    "assistant_role",
    "entity_types",
    "relationship_types",
    "relationship_priorities",
    "topic_priorities",
    "vision_focus_areas",
    "examples",
    "forbidden_terms",
    "allow_legacy_terms",
}

LEGACY_TERMS = (
    "twia",
    "homeowners policy (ho-3)",
    "licensed insurance adjuster",
    "texas department of insurance",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Profile must be a JSON object: {path}")
    return data


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _prompt_str(question: str, default: str) -> str:
    raw = input(f"{question} [{default}]: ").strip()
    return raw or default


def _prompt_list(question: str, default: list[str]) -> list[str]:
    raw = input(f"{question} (comma-separated) [{', '.join(default)}]: ").strip()
    if not raw:
        return default
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]


def _load_or_default(profile_path: Path) -> dict[str, Any]:
    if profile_path.exists():
        profile = _load_json(profile_path)
    else:
        profile = {}
    default_profile = _load_json(PROFILES_DIR / "generic.json")
    merged: dict[str, Any] = dict(default_profile)
    merged.update(profile)
    return merged


def _interactive_profile(profile_path: Path) -> dict[str, Any]:
    profile = _load_or_default(profile_path)
    print(f"Interactive domain wizard -> {profile_path}")
    profile["schema_version"] = 1
    profile["profile_name"] = _prompt_str("Profile name", str(profile["profile_name"]))
    profile["domain_name"] = _prompt_str("Domain name", str(profile["domain_name"]))
    profile["assistant_role"] = _prompt_str(
        "Assistant role", str(profile["assistant_role"])
    )
    profile["entity_types"] = _prompt_list(
        "Entity types", list(profile["entity_types"])
    )
    profile["topic_priorities"] = _prompt_list(
        "Topic priorities", list(profile["topic_priorities"])
    )
    profile["relationship_types"] = _prompt_list(
        "Relationship types", list(profile["relationship_types"])
    )
    profile["relationship_priorities"] = _prompt_list(
        "Relationship priorities", list(profile["relationship_priorities"])
    )
    profile["vision_focus_areas"] = _prompt_list(
        "Vision focus areas", list(profile["vision_focus_areas"])
    )
    profile["examples"] = _prompt_list("Domain examples", list(profile["examples"]))
    profile["forbidden_terms"] = _prompt_list(
        "Forbidden terms (profile-specific)", list(profile["forbidden_terms"])
    )
    allow = _prompt_str(
        "Allow legacy insurance terms? (yes/no)",
        "yes" if profile.get("allow_legacy_terms") else "no",
    ).lower()
    profile["allow_legacy_terms"] = allow in {"y", "yes", "true", "1"}
    return profile


def _validate_profile(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_PROFILE_KEYS - set(profile.keys())
    if missing:
        errors.append(f"Missing profile keys: {', '.join(sorted(missing))}")

    for key in (
        "entity_types",
        "relationship_types",
        "relationship_priorities",
        "topic_priorities",
        "vision_focus_areas",
        "examples",
        "forbidden_terms",
    ):
        value = profile.get(key)
        if not isinstance(value, list) or not all(isinstance(i, str) for i in value):
            errors.append(f"`{key}` must be a list of strings.")
        elif key != "forbidden_terms" and len(value) == 0:
            errors.append(f"`{key}` must not be empty.")

    for key in ("profile_name", "domain_name", "assistant_role"):
        value = str(profile.get(key, "")).strip()
        if not value:
            errors.append(f"`{key}` must be a non-empty string.")

    if not isinstance(profile.get("allow_legacy_terms"), bool):
        errors.append("`allow_legacy_terms` must be boolean.")
    if not isinstance(profile.get("schema_version"), int):
        errors.append("`schema_version` must be an integer.")

    entity_types = [x.strip() for x in profile.get("entity_types", []) if x.strip()]
    lowered = [x.lower() for x in entity_types]
    if len(lowered) != len(set(lowered)):
        errors.append("`entity_types` contains duplicates.")
    relationship_types = [
        x.strip() for x in profile.get("relationship_types", []) if x.strip()
    ]
    rel_lowered = [x.lower() for x in relationship_types]
    if len(rel_lowered) != len(set(rel_lowered)):
        errors.append("`relationship_types` contains duplicates.")

    return errors


def _render_prompt(profile: dict[str, Any], template_text: str) -> str:
    replacements = {
        "{{DOMAIN_NAME}}": profile["domain_name"],
        "{{ASSISTANT_ROLE}}": profile["assistant_role"],
        "{{ENTITY_TYPES}}": ", ".join(profile["entity_types"]),
        "{{RELATIONSHIP_TYPES}}": ", ".join(profile["relationship_types"]),
        "{{RELATIONSHIP_PRIORITIES}}": ", ".join(profile["relationship_priorities"]),
        "{{TOPIC_PRIORITIES}}": ", ".join(profile["topic_priorities"]),
        "{{VISION_FOCUS_AREAS}}": ", ".join(profile["vision_focus_areas"]),
        "{{EXAMPLES}}": "; ".join(profile["examples"]),
        "{{FORBIDDEN_TERMS}}": ", ".join(profile["forbidden_terms"])
        if profile["forbidden_terms"]
        else "none",
    }
    prompt = template_text
    for key, value in replacements.items():
        prompt = prompt.replace(key, value)
    return prompt


def _validate_legacy_terms(prompt_text: str, profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not profile.get("allow_legacy_terms", False):
        lowered = prompt_text.lower()
        explicitly_forbidden = {
            term.lower() for term in profile.get("forbidden_terms", [])
        }
        matched = [
            term
            for term in LEGACY_TERMS
            if term in lowered and term not in explicitly_forbidden
        ]
        if matched:
            errors.append(
                "Legacy domain terms found while allow_legacy_terms=false: "
                + ", ".join(matched)
            )
    return errors


def _validate_rendered_prompt(
    prompt_text: str, profile: dict[str, Any], target: str
) -> list[str]:
    errors: list[str] = []
    errors.extend(_validate_legacy_terms(prompt_text, profile))

    if target in {"entity", "edge"} and "{context}" not in prompt_text:
        errors.append("Prompt must contain `{context}` placeholder.")

    if target == "entity":
        required_tokens = (
            '"entities"',
            '"name"',
            '"type"',
            '"description"',
            '"keywords"',
            '"source_excerpt"',
            '"priority"',
        )
        for token in required_tokens:
            if token not in prompt_text:
                errors.append(f"Missing required schema token in prompt: {token}")
        for entity_type in profile["entity_types"]:
            if entity_type not in prompt_text:
                errors.append(f"Entity type missing from rendered prompt: {entity_type}")

    if target == "edge":
        required_tokens = (
            '"relationships"',
            '"source"',
            '"target"',
            '"type"',
            '"description"',
            '"valid_at"',
        )
        for token in required_tokens:
            if token not in prompt_text:
                errors.append(f"Missing required schema token in prompt: {token}")
        for rel_type in profile["relationship_types"]:
            if rel_type not in prompt_text:
                errors.append(
                    f"Relationship type missing from rendered prompt: {rel_type}"
                )

    if target == "vision":
        required_tokens = ("Answer:", "Rationale:", "Citations:")
        for token in required_tokens:
            if token not in prompt_text:
                errors.append(f"Missing required output section in prompt: {token}")

    if "{context}" in prompt_text:
        try:
            prompt_text.format(context="TEST_CONTEXT")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Prompt cannot be formatted with context: {exc}")

    return errors


def _resolve_profile_path(args: argparse.Namespace) -> Path:
    if args.profile_path:
        return Path(args.profile_path).expanduser().resolve()
    return (PROFILES_DIR / f"{args.profile_name}.json").resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive profile wizard + prompt generator for domain customization."
    )
    parser.add_argument("--profile-name", default="generic", help="Profile file name.")
    parser.add_argument(
        "--profile-path",
        help="Explicit profile path (overrides --profile-name).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run Q&A flow to create/update profile values.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Generate prompt files from template/profile.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate profile and generated prompt contract.",
    )
    parser.add_argument(
        "--targets",
        default="entity,edge,vision",
        help="Comma-separated targets to generate/check: entity,edge,vision.",
    )
    parser.add_argument(
        "--output-path",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Override output path for entity target only.",
    )
    return parser.parse_args()


def _resolve_targets(raw_targets: str) -> list[str]:
    targets = [target.strip().lower() for target in raw_targets.split(",") if target.strip()]
    if not targets:
        raise ValueError("At least one target must be provided.")
    invalid = [target for target in targets if target not in TARGETS]
    if invalid:
        raise ValueError(
            f"Unsupported targets: {', '.join(invalid)}. "
            f"Valid targets: {', '.join(TARGETS.keys())}."
        )
    return targets


def main() -> int:
    args = parse_args()
    profile_path = _resolve_profile_path(args)

    if args.interactive:
        profile = _interactive_profile(profile_path)
        _save_json(profile_path, profile)
        print(f"Saved profile: {profile_path}")
    elif profile_path.exists():
        profile = _load_or_default(profile_path)
    else:
        print(
            f"Profile not found: {profile_path}\n"
            "Use --interactive to create one, or pass --profile-name generic.",
            file=sys.stderr,
        )
        return 1

    profile_errors = _validate_profile(profile)
    if profile_errors:
        for err in profile_errors:
            print(f"[profile error] {err}", file=sys.stderr)
        return 1

    try:
        resolved_targets = _resolve_targets(args.targets)
    except ValueError as exc:
        print(f"[arg error] {exc}", file=sys.stderr)
        return 1

    output_override = Path(args.output_path).expanduser().resolve()
    if len(resolved_targets) > 1 and output_override != DEFAULT_OUTPUT_PATH.resolve():
        print(
            "[arg error] --output-path can only be used when --targets is entity.",
            file=sys.stderr,
        )
        return 1

    rendered_by_target: dict[str, str] = {}
    output_paths: dict[str, Path] = {}
    for target in resolved_targets:
        template_path = TARGETS[target]["template"]
        output_path = TARGETS[target]["output"]
        if target == "entity" and output_override != DEFAULT_OUTPUT_PATH.resolve():
            output_path = output_override

        template_text = template_path.read_text(encoding="utf-8")
        rendered_by_target[target] = _render_prompt(profile, template_text)
        output_paths[target] = output_path

    if args.apply:
        for target in resolved_targets:
            path = output_paths[target]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered_by_target[target], encoding="utf-8")
            print(f"Generated {target} prompt: {path}")

    if args.check or args.apply:
        had_errors = False
        for target in resolved_targets:
            prompt_text = rendered_by_target[target]
            prompt_errors = _validate_rendered_prompt(prompt_text, profile, target)
            if prompt_errors:
                had_errors = True
                for err in prompt_errors:
                    print(f"[{target} prompt error] {err}", file=sys.stderr)
        if had_errors:
            return 1
        print("Validation passed.")

    if not args.apply and not args.check:
        print("No action taken. Use --interactive --apply --check.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
