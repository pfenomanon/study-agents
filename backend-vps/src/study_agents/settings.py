"""Centralized settings loading and validation helpers."""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv

load_dotenv()


class SettingsError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


_GROUP_FIELDS: dict[str, tuple[str, ...]] = {
    "openai": ("openai_api_key",),
    "supabase": ("supabase_url", "supabase_key"),
    "ollama": ("ollama_host",),
}


@dataclass(frozen=True)
class Settings:
    """Immutable settings container sourced from environment variables."""

    openai_api_key: str
    openai_embed_model: str
    supabase_url: str
    supabase_key: str
    supabase_docs_table: str
    supabase_nodes_table: str
    supabase_edges_table: str
    ollama_api_key: str
    ollama_host: str
    reason_model: str
    target_monitor: int
    capture_interval: float
    screenshot_dir: Path
    schema_pdf_path: Path
    schema_default_chunk_size: int
    schema_default_overlap: int
    schema_model_provider: str
    schema_model_name: str
    use_hybrid_retrieval: bool

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables."""
        def _bool(name: str, default: str = "false") -> bool:
            raw = os.getenv(name, default).lower()
            return raw in {"1", "true", "yes", "on"}

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_embed_model=os.getenv(
                "OPENAI_EMBED_MODEL", "text-embedding-3-small"
            ).strip(),
            supabase_url=os.getenv("SUPABASE_URL", "").strip(),
            supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
            supabase_docs_table=os.getenv("SUPABASE_DOCS_TABLE", "documents").strip(),
            supabase_nodes_table=os.getenv("SUPABASE_NODES_TABLE", "kg_nodes").strip(),
            supabase_edges_table=os.getenv("SUPABASE_EDGES_TABLE", "kg_edges").strip(),
            ollama_api_key=os.getenv("OLLAMA_API_KEY", "").strip(),
            ollama_host=os.getenv("OLLAMA_HOST", "https://ollama.com").strip(),
            reason_model=os.getenv("REASON_MODEL", "deepseek-v3.1:671b-cloud").strip(),
            target_monitor=int(os.getenv("TARGET_MONITOR", "2")),
            capture_interval=float(os.getenv("CAPTURE_INTERVAL", "5")),
            screenshot_dir=Path(
                os.getenv("SCREENSHOT_DIR", "data/screenshots")
            ).expanduser().resolve(),
            schema_pdf_path=Path(
                os.getenv("SCHEMA_PDF_PATH", "data/pdf/schema_source.pdf")
            ).expanduser().resolve(),
            schema_default_chunk_size=int(
                os.getenv("SCHEMA_DEFAULT_CHUNK_SIZE", "1200")
            ),
            schema_default_overlap=int(
                os.getenv("SCHEMA_DEFAULT_OVERLAP", "150")
            ),
            schema_model_provider=os.getenv("SCHEMA_MODEL_PROVIDER", "openai").strip(),
            schema_model_name=os.getenv("SCHEMA_MODEL_NAME", "gpt-4o-mini").strip(),
            use_hybrid_retrieval=_bool("USE_HYBRID_RETRIEVAL", "false"),
        )

    def require_groups(self, *groups: str) -> None:
        """Ensure that required configuration groups are populated."""
        missing: list[str] = []
        for group in groups or ("openai", "supabase"):
            fields = _GROUP_FIELDS.get(group)
            if not fields:
                raise SettingsError(f"Unknown configuration group: '{group}'")
            for field in fields:
                if not getattr(self, field):
                    missing.append(field)
        if missing:
            raise SettingsError(
                "Missing required configuration values: "
                + ", ".join(sorted(missing))
            )

    def ensure_directories(self) -> None:
        """Create filesystem directories needed for common workflows."""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.schema_pdf_path.parent.mkdir(parents=True, exist_ok=True)

    def summary(self) -> dict[str, str]:
        """Return a sanitized summary of loaded settings."""
        return {
            "openai_api_key": "set" if self.openai_api_key else "missing",
            "supabase_url": self.supabase_url or "missing",
            "ollama_host": self.ollama_host or "missing",
            "reason_model": self.reason_model,
            "screenshot_dir": str(self.screenshot_dir),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Memoized accessor for process-wide settings."""
    return Settings.from_env()


def validate_cli() -> None:
    """CLI entry point to validate environment configuration."""
    parser = argparse.ArgumentParser(
        description="Validate study-agents configuration and required environment variables."
    )
    parser.add_argument(
        "--groups",
        default="openai,supabase,ollama",
        help="Comma-separated config groups to validate "
        "(default: openai,supabase,ollama)",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a summary of resolved configuration values.",
    )
    args = parser.parse_args()

    groups = tuple(
        part.strip()
        for part in args.groups.split(",")
        if part.strip()
    )

    settings = get_settings()
    try:
        if groups:
            settings.require_groups(*groups)
        else:
            settings.require_groups()
    except SettingsError as exc:
        print(f"❌ Configuration validation failed: {exc}")
        raise SystemExit(1) from exc

    settings.ensure_directories()

    print("✅ Configuration validation succeeded.")
    if args.print_summary:
        for key, value in settings.summary().items():
            print(f"  - {key}: {value}")


if __name__ == "__main__":
    validate_cli()
