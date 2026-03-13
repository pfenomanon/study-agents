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

