from __future__ import annotations

import os
from pathlib import Path


PROMPTS_DIR = Path(
    os.getenv("PROMPTS_DIR", Path(__file__).resolve().parents[2] / "prompts")
)


def load_prompt(filename: str, default: str) -> str:
    """
    Load a prompt text file from the prompts directory.
    Falls back to the provided default string if the file is missing.
    """
    try:
        path = PROMPTS_DIR / filename
        if path.exists():
            return path.read_text(encoding="utf-8").strip() or default
    except OSError:
        pass
    return default


_DEFAULT_REQUIRED_PROMPTS: dict[str, str] = {
    "cag_entity_extraction.txt": (
        "You are an entity extraction expert. Extract key entities from the text.\n"
        "Return JSON: {\"entities\":[{\"name\":\"...\",\"type\":\"...\",\"description\":\"...\"}]}"
    ),
    "cag_relationship_extraction.txt": (
        "You are a relationship extraction expert. Find relationships between entities.\n"
        "Return JSON: {\"relationships\":[{\"source\":\"...\",\"target\":\"...\",\"relationship\":\"related_to\",\"confidence\":0.8}]}"
    ),
    "cag_answer_generation.txt": (
        "You are a grounded assistant. Answer only from the provided context.\n"
        "Format exactly:\n"
        "Answer: ...\n"
        "Rationale: ...\n"
        "Citations: <doc ids or NONE>"
    ),
    "cag_cluster_topic.txt": "Generate a short topic name (2-3 words) for the text.",
    "cag_grounding_verifier_system.txt": (
        "You verify whether an answer is supported by context.\n"
        "Return JSON: {\"supported\": true|false, \"reason\":\"...\", \"unsupported_claims\":[...]}"
    ),
    "cag_grounding_repair_system.txt": (
        "Repair answer format for strict grounding.\n"
        "Keep only supported claims and valid citations from allowed ids."
    ),
    "vision_reasoning.txt": (
        "You are a grounded assistant.\n"
        "Answer only from provided context and include citations when available."
    ),
}


def load_required_prompt(filename: str) -> str:
    """
    Load a prompt file when available, with safe built-in fallbacks.

    This keeps older client installs operational when prompt assets are not
    shipped alongside the Python package.
    """
    path = PROMPTS_DIR / filename
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    return _DEFAULT_REQUIRED_PROMPTS.get(
        filename,
        "You are a helpful assistant. Follow instructions and stay grounded in provided context.",
    )
