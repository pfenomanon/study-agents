#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:  # pragma: no cover
    def _load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = ROOT / "domain" / "profiles"
PROMPTS_DIR = ROOT / "prompts"
TEMPLATES_DIR = PROMPTS_DIR / "templates"
DEFAULT_OUTPUT_PATH = PROMPTS_DIR / "kg_entity_extraction.txt"
SRC_DIR = ROOT / "src"

TARGETS = {
    "entity": {
        "family": "kg_entity_extractor",
        "template": TEMPLATES_DIR / "kg_entity_extraction.base.txt",
        "output": PROMPTS_DIR / "kg_entity_extraction.txt",
    },
    "edge": {
        "family": "kg_edge_mapper",
        "template": TEMPLATES_DIR / "kg_edge_extraction.base.txt",
        "output": PROMPTS_DIR / "kg_edge_extraction.txt",
    },
    "vision": {
        "family": "vision_reasoner",
        "template": TEMPLATES_DIR / "vision_reasoning.base.txt",
        "output": PROMPTS_DIR / "vision_reasoning.txt",
    },
    "cag_answer": {
        "family": "cag_answer_synthesizer",
        "template": TEMPLATES_DIR / "cag_answer_generation.base.txt",
        "output": PROMPTS_DIR / "cag_answer_generation.txt",
    },
    "scenario_structurer": {
        "family": "scenario_structurer",
        "template": TEMPLATES_DIR / "scenario_answer_structuring.base.txt",
        "output": PROMPTS_DIR / "scenario_answer_structuring.txt",
    },
}

FAMILY_SPECS: dict[str, dict[str, Any]] = {
    "kg_entity_extractor": {
        "description": "Extract canonical entities from retrieved context for graph ingestion.",
        "slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "ENTITY_TYPES",
            "TOPIC_PRIORITIES",
            "EXAMPLES",
            "FORBIDDEN_TERMS",
        },
        "required_template_slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "ENTITY_TYPES",
            "TOPIC_PRIORITIES",
            "EXAMPLES",
            "FORBIDDEN_TERMS",
        },
    },
    "kg_edge_mapper": {
        "description": "Extract ontology-constrained graph edges from retrieved context.",
        "slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "RELATIONSHIP_TYPES",
            "RELATIONSHIP_PRIORITIES",
            "TOPIC_PRIORITIES",
        },
        "required_template_slots": {
            "DOMAIN_NAME",
            "RELATIONSHIP_TYPES",
            "RELATIONSHIP_PRIORITIES",
        },
    },
    "vision_reasoner": {
        "description": "Grounded screenshot QA with abstention and strict citation contract.",
        "slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "VISION_FOCUS_AREAS",
            "RELATIONSHIP_PRIORITIES",
            "TOPIC_PRIORITIES",
        },
        "required_template_slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "VISION_FOCUS_AREAS",
        },
    },
    "cag_answer_synthesizer": {
        "description": "Synthesize practitioner-facing final answers from doc+graph retrieval.",
        "slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "TOPIC_PRIORITIES",
            "VISION_FOCUS_AREAS",
            "EXAMPLES",
            "FORBIDDEN_TERMS",
        },
        "required_template_slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "TOPIC_PRIORITIES",
        },
    },
    "scenario_structurer": {
        "description": "Transform free-form answer text into strict scenario workflow JSON.",
        "slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "DOMAIN_EXPERTISE",
            "TOPIC_PRIORITIES",
            "VISION_FOCUS_AREAS",
        },
        "required_template_slots": {
            "DOMAIN_NAME",
            "ASSISTANT_ROLE",
            "TOPIC_PRIORITIES",
        },
    },
}

SLOT_VALUE_KEYS = {
    "DOMAIN_NAME",
    "ASSISTANT_ROLE",
    "DOMAIN_EXPERTISE",
    "ENTITY_TYPES",
    "RELATIONSHIP_TYPES",
    "RELATIONSHIP_PRIORITIES",
    "TOPIC_PRIORITIES",
    "VISION_FOCUS_AREAS",
    "EXAMPLES",
    "FORBIDDEN_TERMS",
}

GRAPHITI_CAG_KNOWLEDGE_BRIEF = (
    "Graphiti context graph fundamentals to preserve:\n"
    "- Temporal context graph model: entities (nodes), facts/relationships (edges), and episodes (provenance).\n"
    "- Facts have validity windows and can be invalidated when superseded; history must be preserved.\n"
    "- Retrieval is hybrid: semantic + keyword + graph traversal.\n"
    "- Prefer evidence-grounded answers from retrieved docs/graph context over generic model priors.\n"
    "- Respect source lineage: tie claims to retrieved snippets, graph relationships, and scenario metadata.\n"
    "- Handle conflicts by surfacing uncertainty/temporal ambiguity rather than guessing.\n"
    "- Keep outputs operational for agent workflows while remaining faithful to retrieved evidence."
)

REQUIRED_PROFILE_KEYS = {
    "schema_version",
    "profile_name",
    "domain_name",
    "assistant_role",
    "domain_expertise",
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

def _load_env_file(path: Path) -> None:
    """Load .env values into process env with optional python-dotenv support."""
    loaded = _load_dotenv(path)
    if loaded:
        return
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _load_env_sources(explicit_env_file: str | None) -> list[Path]:
    """Load env values from explicit/default locations, preserving existing env vars."""
    candidates: list[Path] = []
    if explicit_env_file:
        candidates.append(Path(explicit_env_file).expanduser().resolve())
    candidates.extend(
        [
            Path.cwd() / ".env",
            ROOT / ".env",
            ROOT.parent / ".env",
            Path("/home/study-agents/.env"),
        ]
    )
    loaded: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        _load_env_file(resolved)
        loaded.append(resolved)
    return loaded


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


def _seed_domain_from_profile_name(profile_name: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", profile_name).strip()
    return re.sub(r"\s+", " ", cleaned) or "general subject-matter"


def _normalize_string_list(value: Any, fallback: list[str]) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        items = [str(part).strip() for part in value]
    else:
        items = []
    cleaned = [item for item in items if item]
    return cleaned or list(fallback)


def _sanitize_profile_text(value: str) -> str:
    cleaned = re.sub(r"(?i)\bexam helper\b", "subject-matter-expert assistant", value)
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_profile_candidate(
    candidate: dict[str, Any], fallback_profile: dict[str, Any]
) -> dict[str, Any]:
    normalized = dict(fallback_profile)
    list_keys = (
        "domain_expertise",
        "entity_types",
        "relationship_types",
        "relationship_priorities",
        "topic_priorities",
        "vision_focus_areas",
        "examples",
        "forbidden_terms",
    )
    for key in list_keys:
        normalized[key] = [
            _sanitize_profile_text(item)
            for item in _normalize_string_list(
            candidate.get(key), list(fallback_profile.get(key, []))
            )
        ]

    for key in ("profile_name", "domain_name", "assistant_role"):
        raw = _sanitize_profile_text(
            str(candidate.get(key, fallback_profile.get(key, ""))).strip()
        )
        normalized[key] = raw or _sanitize_profile_text(
            str(fallback_profile.get(key, "")).strip()
        )

    try:
        normalized["schema_version"] = int(
            candidate.get("schema_version", fallback_profile.get("schema_version", 1))
        )
    except (TypeError, ValueError):
        normalized["schema_version"] = int(fallback_profile.get("schema_version", 1))
    allow_legacy = candidate.get(
        "allow_legacy_terms", fallback_profile.get("allow_legacy_terms", False)
    )
    if isinstance(allow_legacy, str):
        allow_legacy = allow_legacy.strip().lower() in {"1", "true", "yes", "y"}
    normalized["allow_legacy_terms"] = bool(allow_legacy)
    return normalized


def _bootstrap_profile_defaults(profile_name: str, domain_seed: str) -> dict[str, Any]:
    base = _load_json(PROFILES_DIR / "generic.json")
    seed = domain_seed.strip() or "general subject-matter"
    profile = dict(base)
    profile["profile_name"] = profile_name
    profile["domain_name"] = seed
    profile["assistant_role"] = f"subject-matter-expert assistant for {seed}"
    profile["domain_expertise"] = [
        f"core concepts, terminology, and constraints in {seed}",
        f"evidence-grounded reasoning and decision support for {seed}",
        f"workflow sequencing, dependencies, and exception handling in {seed}",
        f"risk, limitation, and compliance awareness relevant to {seed}",
    ]
    profile["topic_priorities"] = [
        f"core definitions and terminology in {seed}",
        f"requirements, prerequisites, and constraints in {seed}",
        f"roles, responsibilities, and workflow handoffs in {seed}",
        f"timelines, deadlines, and operational dependencies in {seed}",
        f"risks, limitations, and exceptions in {seed}",
    ]
    profile["vision_focus_areas"] = [
        f"context-grounded correctness for {seed} questions",
        "clear next-step guidance for practitioners",
        "explicit assumptions, limitations, and abstention when evidence is missing",
    ]
    profile["examples"] = [
        f"Primary workflow or process in {seed}",
        f"Eligibility or prerequisite condition in {seed}",
        f"A common exception or limitation in {seed}",
        f"Responsible role and required action in {seed}",
        f"Critical timeline milestone in {seed}",
    ]
    profile["forbidden_terms"] = []
    profile["allow_legacy_terms"] = False
    return _normalize_profile_candidate(profile, base)


def _interactive_profile(profile_path: Path) -> dict[str, Any]:
    profile = _load_or_default(profile_path)
    print(f"Interactive domain wizard -> {profile_path}")
    profile["schema_version"] = 1
    profile["profile_name"] = _prompt_str("Profile name", str(profile["profile_name"]))
    profile["domain_name"] = _prompt_str("Domain name", str(profile["domain_name"]))
    profile["assistant_role"] = _prompt_str(
        "Assistant role", str(profile["assistant_role"])
    )
    profile["domain_expertise"] = _prompt_list(
        "Domain expertise lens", list(profile["domain_expertise"])
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
        "domain_expertise",
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


_TEMPLATE_SLOT_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def _extract_template_slots(template_text: str) -> set[str]:
    return set(_TEMPLATE_SLOT_RE.findall(template_text))


def _slot_values_from_profile(profile: dict[str, Any]) -> dict[str, str]:
    return {
        "DOMAIN_NAME": str(profile["domain_name"]),
        "ASSISTANT_ROLE": str(profile["assistant_role"]),
        "DOMAIN_EXPERTISE": ", ".join(profile["domain_expertise"]),
        "ENTITY_TYPES": ", ".join(profile["entity_types"]),
        "RELATIONSHIP_TYPES": ", ".join(profile["relationship_types"]),
        "RELATIONSHIP_PRIORITIES": ", ".join(profile["relationship_priorities"]),
        "TOPIC_PRIORITIES": ", ".join(profile["topic_priorities"]),
        "VISION_FOCUS_AREAS": ", ".join(profile["vision_focus_areas"]),
        "EXAMPLES": "; ".join(profile["examples"]),
        "FORBIDDEN_TERMS": ", ".join(profile["forbidden_terms"])
        if profile["forbidden_terms"]
        else "none",
    }


def _validate_template_slots(template_text: str, family: str) -> list[str]:
    errors: list[str] = []
    slots = _extract_template_slots(template_text)
    spec = FAMILY_SPECS[family]
    required = set(spec["required_template_slots"])
    missing = required - slots
    if missing:
        errors.append(
            f"Template missing required slots for family '{family}': "
            + ", ".join(sorted(missing))
        )
    unknown = slots - SLOT_VALUE_KEYS
    if unknown:
        errors.append(
            "Template contains unknown slots: " + ", ".join(sorted(unknown))
        )
    disallowed = slots - set(spec["slots"])
    if disallowed:
        errors.append(
            f"Template uses slots not allowed by family '{family}': "
            + ", ".join(sorted(disallowed))
        )
    return errors


def _render_prompt(
    profile: dict[str, Any],
    template_text: str,
    *,
    slot_overrides: dict[str, str] | None = None,
) -> str:
    values = _slot_values_from_profile(profile)
    if slot_overrides:
        for key, value in slot_overrides.items():
            if key in values:
                values[key] = value.strip()
    prompt = template_text
    for slot in sorted(_extract_template_slots(template_text)):
        prompt = prompt.replace(f"{{{{{slot}}}}}", values.get(slot, ""))
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
        required_tokens = (
            "Question-type policy (adaptive):",
            "Binary (True/False, Yes/No)",
            "Option-based",
            "Open-ended",
            "Hard output contract (must follow exactly):",
            "Output exactly 3 lines.",
            "Answer:",
            "Rationale:",
            "Citations:",
            "Insufficient grounded evidence to answer from provided context.",
            "Citations: NONE",
        )
        for token in required_tokens:
            if token not in prompt_text:
                errors.append(f"Missing required output section in prompt: {token}")

    if target == "cag_answer":
        required_tokens = (
            "subject-matter-expert",
            "Graphiti",
            "context graph",
            "Question-type policy (adaptive):",
            "Grounding policy:",
            "For abstention:",
        )
        for token in required_tokens:
            if token not in prompt_text:
                errors.append(f"Missing required output section in prompt: {token}")
        if "exam helper" in prompt_text.lower():
            errors.append("Prompt must not use 'exam helper' language.")

    if target == "scenario_structurer":
        required_tokens = (
            "Return JSON only with this exact structure:",
            '"summary"',
            '"recommended_steps"',
            '"analysis"',
            '"documentation_checklist"',
            '"citations"',
            "Do not add markdown fences",
        )
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


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _ensure_context_format_safe(prompt_text: str) -> str:
    """Escape non-context braces so `.format(context=...)` is safe."""
    try:
        prompt_text.format(context="TEST_CONTEXT")
        return prompt_text
    except Exception:
        marker = "__DOMAIN_WIZARD_CONTEXT__"
        safe = prompt_text.replace("{context}", marker)
        safe = safe.replace("{", "{{").replace("}", "}}")
        safe = safe.replace(marker, "{context}")
        return safe


def _target_constraints(target: str, profile: dict[str, Any]) -> str:
    if target == "entity":
        return (
            "- Keep `{context}` exactly once.\n"
            "- Keep JSON schema fields: entities, name, type, description, keywords, source_excerpt, priority.\n"
            "- Use double braces for literal JSON braces (e.g., `{{` and `}}`) so Python `.format(context=...)` works.\n"
            f"- Allowed entity types: {', '.join(profile['entity_types'])}.\n"
            "- Return plain prompt text only. No markdown fences."
        )
    if target == "edge":
        return (
            "- Keep `{context}` exactly once.\n"
            "- Keep JSON schema fields: relationships, source, target, type, description, valid_at.\n"
            "- Use double braces for literal JSON braces (e.g., `{{` and `}}`) so Python `.format(context=...)` works.\n"
            f"- Allowed relationship types: {', '.join(profile['relationship_types'])}.\n"
            "- Return plain prompt text only. No markdown fences."
        )
    if target == "cag_answer":
        return (
            "- Keep role as `subject-matter-expert` (never `exam helper`).\n"
            "- Keep explicit Graphiti context graph framing (entities, relationships, temporal/provenance awareness).\n"
            "- Keep responses grounded in retrieved docs + graph context, not model priors.\n"
            "- Keep adaptive question-type policy: binary, option-based, open-ended.\n"
            "- Keep grounding policy and abstention behavior explicit.\n"
            "- Keep practitioner-facing action style (`you` language).\n"
            "- Return plain prompt text only. No markdown fences."
        )
    if target == "scenario_structurer":
        return (
            "- Output must enforce strict JSON-only transformation behavior.\n"
            "- Keep exact required keys: summary, recommended_steps, analysis, documentation_checklist, citations.\n"
            "- For documentation_checklist entries keep keys: item, status, notes.\n"
            "- For citations entries keep keys: source, details.\n"
            "- Return plain prompt text only. No markdown fences."
        )
    return (
        "- Preserve adaptive question typing: Binary, Option-based, Open-ended.\n"
        "- Preserve strict abstention behavior when context is insufficient.\n"
        "- Keep explicit output sections: `Answer:`, `Rationale:`, `Citations:`.\n"
        "- Enforce hard contract: exactly 3 lines and `Citations: NONE` when abstaining.\n"
        "- Return plain prompt text only. No markdown fences."
    )


def _build_openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Export it or run without --use-ai."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Missing dependency: openai package is required for --use-ai."
        ) from exc
    return OpenAI(api_key=api_key)


def _resolve_runtime_via_cag_agent(
    *,
    platform: str | None,
    model: str | None,
    ollama_target: str | None,
) -> dict[str, Any]:
    """Use shared backend runtime resolution so wizard matches API/vision behavior."""
    sys.path.insert(0, str(SRC_DIR))
    try:
        from study_agents.cag_agent import CAGAgent
    except Exception as exc:  # pragma: no cover
        return _resolve_runtime_fallback(
            platform=platform,
            model=model,
            ollama_target=ollama_target,
            resolver_error=exc,
        )
    try:
        agent = CAGAgent()
        runtime = agent.resolve_reasoning_runtime(
            platform=platform,
            model=model,
            ollama_target=ollama_target,
        )
        runtime["_resolver"] = "cag_agent"
        return runtime
    except Exception as exc:  # pragma: no cover
        return _resolve_runtime_fallback(
            platform=platform,
            model=model,
            ollama_target=ollama_target,
            resolver_error=exc,
        )


def _resolve_runtime_fallback(
    *,
    platform: str | None,
    model: str | None,
    ollama_target: str | None,
    resolver_error: Exception | None = None,
) -> dict[str, Any]:
    """Fallback resolver mirroring CAGAgent runtime logic when imports are unavailable."""
    platform_raw = (platform or "").strip().lower()
    model_raw = (model or "").strip()

    if not platform_raw:
        if model_raw and ":" in model_raw:
            platform_raw = "ollama"
        else:
            platform_raw = (
                os.getenv("REASON_PLATFORM", "openai") or "openai"
            ).strip().lower()

    if platform_raw not in {"openai", "ollama"}:
        raise RuntimeError("Invalid platform. Expected 'openai' or 'ollama'.")

    resolved_model = model_raw or (os.getenv("REASON_MODEL", "").strip() or "gpt-4o-mini")

    runtime: dict[str, Any] = {
        "platform": platform_raw,
        "model": resolved_model,
        "ollama_target": None,
        "ollama_host": None,
        "ollama_api_key": None,
        "_resolver": "fallback",
    }
    if resolver_error is not None:
        runtime["_resolver_error"] = str(resolver_error)

    if platform_raw == "ollama":
        target = (ollama_target or os.getenv("OLLAMA_TARGET", "cloud")).strip().lower()
        if target not in {"local", "cloud"}:
            raise RuntimeError("Invalid ollama_target. Expected 'local' or 'cloud'.")

        if target == "local":
            host = (os.getenv("OLLAMA_LOCAL_HOST") or "http://127.0.0.1:11434").strip()
            api_key = (os.getenv("OLLAMA_LOCAL_API_KEY") or "").strip() or None
        else:
            host = (
                os.getenv("OLLAMA_CLOUD_HOST")
                or os.getenv("OLLAMA_HOST")
                or ""
            ).strip()
            api_key = (
                os.getenv("OLLAMA_CLOUD_API_KEY")
                or os.getenv("OLLAMA_API_KEY")
                or ""
            ).strip() or None

        if not host:
            raise RuntimeError("Ollama host is not configured for selected target.")

        runtime.update(
            {
                "ollama_target": target,
                "ollama_host": host,
                "ollama_api_key": api_key,
            }
        )
    return runtime


def _generate_with_ollama(
    *,
    runtime: dict[str, Any],
    system_msg: str,
    user_msg: str,
    temperature: float,
) -> str:
    try:
        import ollama
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Missing dependency: ollama package is required.") from exc

    host = (runtime.get("ollama_host") or "").strip()
    if not host:
        raise RuntimeError("Ollama host is not configured.")

    api_key = (runtime.get("ollama_api_key") or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    client = ollama.Client(host=host, headers=headers)
    def _extract_content(response: Any) -> str:
        if isinstance(response, dict):
            message = response.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if content:
                    return str(content)
            for key in ("response", "content"):
                value = response.get(key)
                if value:
                    return str(value)
            return ""

        message = getattr(response, "message", None)
        if message is not None:
            content = getattr(message, "content", None)
            if content:
                return str(content)

        for key in ("response", "content"):
            value = getattr(response, key, None)
            if value:
                return str(value)

        dump_fn = getattr(response, "model_dump", None)
        if callable(dump_fn):
            dumped = dump_fn()
            if isinstance(dumped, dict):
                return _extract_content(dumped)
        return ""

    def _invoke(model_name: str) -> str:
        response = client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            options={"temperature": temperature},
        )
        return _extract_content(response).strip()

    model_name = str(runtime["model"]).strip()
    candidates: list[str] = [model_name]
    if model_name.endswith("-cloud"):
        candidates.append(model_name[: -len("-cloud")])
    if model_name.endswith("-local"):
        candidates.append(model_name[: -len("-local")])

    tried: list[str] = []
    for candidate in candidates:
        if candidate in tried:
            continue
        tried.append(candidate)
        try:
            content = _invoke(candidate)
            if content:
                if candidate != model_name:
                    print(
                        f"[ai warn] ollama model fallback: '{model_name}' -> '{candidate}'"
                    )
                return content
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue

    # Try matching by base prefix if direct candidates failed.
    try:
        base = model_name.split(":")[0]
        listed = client.list()
        available: list[str] = []
        models = getattr(listed, "models", None)
        if models is None and isinstance(listed, dict):
            models = listed.get("models")
        if models:
            for item in models:
                name = None
                if isinstance(item, dict):
                    name = item.get("model") or item.get("name")
                else:
                    name = getattr(item, "model", None) or getattr(item, "name", None)
                if isinstance(name, str):
                    available.append(name)
        for candidate in available:
            if candidate in tried:
                continue
            if not candidate.startswith(base):
                continue
            tried.append(candidate)
            try:
                content = _invoke(candidate)
                if content:
                    print(
                        f"[ai warn] ollama model fallback: '{model_name}' -> '{candidate}'"
                    )
                    runtime["model"] = candidate
                    return content
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    if "last_exc" in locals():
        raise RuntimeError(f"Ollama generation failed after trying {tried}: {last_exc}")
    raise RuntimeError(f"Ollama returned empty content after trying {tried}.")


def _generate_profile_with_ai(
    *,
    runtime: dict[str, Any],
    temperature: float,
    profile_name: str,
    domain_seed: str,
    base_profile: dict[str, Any],
) -> dict[str, Any]:
    system_msg = (
        "You are a domain onboarding architect. "
        "Return strict JSON only with key `profile`."
    )
    user_msg = (
        "Create a complete domain profile JSON object for a prompt wizard.\n"
        "Output JSON only in this shape: {\"profile\": {...}}.\n"
        "Do not include markdown.\n\n"
        f"profile_name: {profile_name}\n"
        f"domain_seed: {domain_seed}\n\n"
        "Required keys in profile:\n"
        "- schema_version (int)\n"
        "- profile_name (string)\n"
        "- domain_name (string)\n"
        "- assistant_role (string)\n"
        "- domain_expertise (list[str], min 4)\n"
        "- entity_types (list[str], min 8)\n"
        "- relationship_types (list[str], min 6)\n"
        "- relationship_priorities (list[str], min 4)\n"
        "- topic_priorities (list[str], min 5)\n"
        "- vision_focus_areas (list[str], min 3)\n"
        "- examples (list[str], min 5)\n"
        "- forbidden_terms (list[str], can be empty)\n"
        "- allow_legacy_terms (bool)\n\n"
        "Rules:\n"
        "- Keep content domain-neutral unless the domain_seed explicitly indicates a specific industry.\n"
        "- Make the profile practical for retrieval + graph extraction + grounded QA.\n"
        "- Preserve strong evidence-grounding behavior.\n"
        "- Do not use the phrase 'exam helper'.\n\n"
        "Base fallback profile:\n"
        f"{json.dumps(base_profile, indent=2)}\n"
    )

    platform = runtime.get("platform")
    if platform == "openai":
        client = _build_openai_client()
        completion = client.chat.completions.create(
            model=runtime["model"],
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        )
        content = completion.choices[0].message.content or ""
    elif platform == "ollama":
        content = _generate_with_ollama(
            runtime=runtime,
            system_msg=system_msg,
            user_msg=user_msg,
            temperature=temperature,
        )
    else:
        raise RuntimeError(f"Unsupported runtime platform: {platform}")

    payload = _extract_json_object(content or "")
    candidate = payload.get("profile")
    if not isinstance(candidate, dict):
        raise RuntimeError("AI JSON must include object key `profile`.")
    merged = dict(base_profile)
    merged.update(candidate)
    normalized = _normalize_profile_candidate(merged, base_profile)
    normalized["profile_name"] = profile_name
    return normalized


def _extract_json_object(text: str) -> dict[str, Any]:
    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise RuntimeError("AI returned empty content.")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise RuntimeError("AI did not return a JSON object.")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise RuntimeError("AI JSON payload must be an object.")
    return data


def _validate_slot_overrides(
    *,
    slot_overrides: dict[str, Any],
    allowed_slots: set[str],
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for key, raw in slot_overrides.items():
        if key not in allowed_slots:
            continue
        value = str(raw).strip()
        if not value:
            continue
        if "{{" in value or "}}" in value:
            continue
        validated[key] = value
    return validated


def _generate_slot_overrides_with_ai(
    *,
    runtime: dict[str, Any],
    temperature: float,
    target: str,
    profile: dict[str, Any],
    template_text: str,
    current_slot_values: dict[str, str],
) -> dict[str, str]:
    family = str(TARGETS[target]["family"])
    family_spec = FAMILY_SPECS[family]
    allowed_slots = sorted(_extract_template_slots(template_text) & set(family_spec["slots"]))

    if target == "cag_answer":
        system_msg = (
            "You are a principal CAG architect and Graphiti context graph expert. "
            "You must return strict JSON only."
        )
    else:
        system_msg = (
            "You are a senior prompt engineer for agentic systems. "
            "You must return strict JSON only."
        )

    knowledge_brief = GRAPHITI_CAG_KNOWLEDGE_BRIEF if target == "cag_answer" else ""
    user_msg = (
        "Refine slot values for a fixed prompt scaffold.\n"
        "Do NOT rewrite structure, contracts, schema keys, or placeholders.\n"
        "Return only JSON like {\"slot_overrides\": {\"SLOT\": \"value\"}}.\n\n"
        f"Target: {target}\n"
        f"Family: {family}\n"
        f"Family description: {family_spec['description']}\n"
        f"Allowed slots: {', '.join(allowed_slots)}\n\n"
        "Profile JSON:\n"
        f"{json.dumps(profile, indent=2)}\n\n"
        "Base template:\n"
        f"{template_text}\n\n"
        "Current slot values:\n"
        f"{json.dumps(current_slot_values, indent=2)}\n\n"
        "Constraints:\n"
        f"{_target_constraints(target, profile)}\n\n"
        "Rules:\n"
        "- Keep language concise and domain-adapted.\n"
        "- Keep hard output contracts unchanged.\n"
        "- Do not include markdown fences or explanations.\n"
    )
    if knowledge_brief:
        user_msg += "\nReference knowledge brief:\n" + knowledge_brief + "\n"

    platform = runtime.get("platform")
    if platform == "openai":
        client = _build_openai_client()
        completion = client.chat.completions.create(
            model=runtime["model"],
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        )
        content = completion.choices[0].message.content or ""
    elif platform == "ollama":
        content = _generate_with_ollama(
            runtime=runtime,
            system_msg=system_msg,
            user_msg=user_msg,
            temperature=temperature,
        )
    else:
        raise RuntimeError(f"Unsupported runtime platform: {platform}")

    payload = _extract_json_object(content or "")
    overrides_raw = payload.get("slot_overrides")
    if not isinstance(overrides_raw, dict):
        raise RuntimeError("AI JSON must include object key `slot_overrides`.")
    overrides = _validate_slot_overrides(
        slot_overrides=overrides_raw,
        allowed_slots=set(allowed_slots),
    )
    return overrides


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive profile wizard + prompt generator for domain customization."
    )
    parser.add_argument("--profile-name", default="generic", help="Profile file name.")
    parser.add_argument(
        "--quickstart",
        action="store_true",
        help="Auto-create missing profile from profile name/domain seed.",
    )
    parser.add_argument(
        "--domain",
        dest="domain_seed",
        default=None,
        help="Optional domain phrase used by --quickstart (e.g. 'aws cloud security').",
    )
    parser.add_argument(
        "--profile-path",
        help="Explicit profile path (overrides --profile-name).",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional .env file path for runtime/API keys.",
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
        default="entity,edge,vision,cag_answer,scenario_structurer",
        help=(
            "Comma-separated targets to generate/check: "
            "entity,edge,vision,cag_answer,scenario_structurer."
        ),
    )
    parser.add_argument(
        "--output-path",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Override output path for entity target only.",
    )
    parser.add_argument(
        "--use-ai",
        action="store_true",
        help="Use provider/model runtime (openai or ollama) to draft prompts before validation.",
    )
    parser.add_argument(
        "--platform",
        choices=["openai", "ollama"],
        default=None,
        help="Reasoning platform override (same semantics as vision/API runtime).",
    )
    parser.add_argument(
        "--model",
        "--ai-model",
        dest="ai_model",
        default=None,
        help="Reasoning model override (same semantics as vision/API runtime).",
    )
    parser.add_argument(
        "--ollama-target",
        choices=["local", "cloud"],
        default=None,
        help="When platform=ollama, choose local or cloud routing.",
    )
    parser.add_argument(
        "--temperature",
        "--ai-temperature",
        dest="ai_temperature",
        type=float,
        default=0.2,
        help="Sampling temperature for --use-ai.",
    )
    parser.add_argument(
        "--no-ai-fallback",
        action="store_true",
        help="Fail if AI output is invalid instead of falling back to deterministic prompt.",
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
    if args.interactive and args.quickstart:
        print(
            "[arg error] --interactive and --quickstart cannot be used together.",
            file=sys.stderr,
        )
        return 1

    loaded_env_files = _load_env_sources(args.env_file)
    if loaded_env_files:
        print(
            "Loaded env files: "
            + ", ".join(str(path) for path in loaded_env_files)
        )
    profile_path = _resolve_profile_path(args)
    ai_client = None

    if args.interactive:
        profile = _interactive_profile(profile_path)
        _save_json(profile_path, profile)
        print(f"Saved profile: {profile_path}")
    elif profile_path.exists():
        profile = _load_or_default(profile_path)
    elif args.quickstart:
        seed = (args.domain_seed or _seed_domain_from_profile_name(args.profile_name)).strip()
        profile = _bootstrap_profile_defaults(args.profile_name, seed)
        if args.use_ai:
            try:
                ai_client = _resolve_runtime_via_cag_agent(
                    platform=args.platform,
                    model=args.ai_model,
                    ollama_target=args.ollama_target,
                )
                print(
                    "AI runtime resolved: "
                    f"platform={ai_client.get('platform')} "
                    f"model={ai_client.get('model')} "
                    f"ollama_target={ai_client.get('ollama_target') or '-'} "
                    f"resolver={ai_client.get('_resolver') or 'unknown'}"
                )
                profile = _generate_profile_with_ai(
                    runtime=ai_client,
                    temperature=args.ai_temperature,
                    profile_name=args.profile_name,
                    domain_seed=seed,
                    base_profile=profile,
                )
            except Exception as exc:  # noqa: BLE001
                if args.no_ai_fallback:
                    print(f"[ai error] quickstart-profile: {exc}", file=sys.stderr)
                    return 1
                print(
                    f"[ai warn] quickstart-profile: {exc}. Using deterministic profile defaults.",
                    file=sys.stderr,
                )
        _save_json(profile_path, profile)
        print(f"Created profile via quickstart: {profile_path}")
    else:
        print(
            f"Profile not found: {profile_path}\n"
            "Use --interactive or --quickstart to create one, or pass --profile-name generic.",
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

    if args.use_ai and ai_client is None:
        try:
            ai_client = _resolve_runtime_via_cag_agent(
                platform=args.platform,
                model=args.ai_model,
                ollama_target=args.ollama_target,
            )
            print(
                "AI runtime resolved: "
                f"platform={ai_client.get('platform')} "
                f"model={ai_client.get('model')} "
                f"ollama_target={ai_client.get('ollama_target') or '-'} "
                f"resolver={ai_client.get('_resolver') or 'unknown'}"
            )
        except RuntimeError as exc:
            print(f"[ai error] {exc}", file=sys.stderr)
            return 1

    rendered_by_target: dict[str, str] = {}
    output_paths: dict[str, Path] = {}
    for target in resolved_targets:
        target_spec = TARGETS[target]
        template_path = Path(target_spec["template"])
        output_path = Path(target_spec["output"])
        family = str(target_spec["family"])
        if target == "entity" and output_override != DEFAULT_OUTPUT_PATH.resolve():
            output_path = output_override

        template_text = template_path.read_text(encoding="utf-8")
        template_errors = _validate_template_slots(template_text, family)
        if template_errors:
            for err in template_errors:
                print(f"[template error] {target}: {err}", file=sys.stderr)
            return 1

        deterministic_prompt = _render_prompt(profile, template_text)
        rendered_prompt = deterministic_prompt
        if args.use_ai and ai_client is not None:
            try:
                current_slot_values = _slot_values_from_profile(profile)
                slot_overrides = _generate_slot_overrides_with_ai(
                    runtime=ai_client,
                    temperature=args.ai_temperature,
                    target=target,
                    profile=profile,
                    template_text=template_text,
                    current_slot_values=current_slot_values,
                )
                candidate = _render_prompt(
                    profile,
                    template_text,
                    slot_overrides=slot_overrides,
                )
                # Defensive normalization if the model modifies placeholder style.
                candidate = re.sub(r"\{\{+\s*context\s*\}\}+", "{context}", candidate)
                if target in {"entity", "edge"}:
                    candidate = _ensure_context_format_safe(candidate)
                candidate_errors = _validate_rendered_prompt(candidate, profile, target)
                if candidate_errors:
                    error_blob = "; ".join(candidate_errors)
                    raise RuntimeError(f"AI prompt failed validation: {error_blob}")
                rendered_prompt = candidate
                print(
                    f"AI generated {target} prompt using "
                    f"{ai_client.get('platform')}/{ai_client.get('model')}"
                )
            except Exception as exc:  # noqa: BLE001
                if args.no_ai_fallback:
                    print(f"[ai error] {target}: {exc}", file=sys.stderr)
                    return 1
                print(
                    f"[ai warn] {target}: {exc}. Falling back to deterministic template output.",
                    file=sys.stderr,
                )
        rendered_by_target[target] = rendered_prompt
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
