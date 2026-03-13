from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..config import (
    OPENAI_API_KEY,
    OPENAI_EMBED_MODEL,
    SUPABASE_DOCS_TABLE,
    SUPABASE_EDGES_TABLE,
    SUPABASE_KEY,
    SUPABASE_NODES_TABLE,
    SUPABASE_URL,
)


class ConfigurationError(RuntimeError):
    """Raised when the environment does not contain the expected settings."""


@dataclass(slots=True)
class IngestionConfig:
    supabase_url: str
    supabase_key: str
    documents_table: str = SUPABASE_DOCS_TABLE
    nodes_table: str = SUPABASE_NODES_TABLE
    edges_table: str = SUPABASE_EDGES_TABLE
    episodes_table: str = "kg_episodes"
    embeddings_model: str = OPENAI_EMBED_MODEL
    store_raw_episode_content: bool = True

    @classmethod
    def from_env(cls, **overrides: str) -> "IngestionConfig":
        supabase_url = overrides.get("supabase_url") or SUPABASE_URL
        supabase_key = overrides.get("supabase_key") or SUPABASE_KEY

        if not supabase_url or not supabase_key:
            raise ConfigurationError(
                "SUPABASE_URL and SUPABASE_KEY must be configured before using the revamped ingestion pipeline."
            )

        return cls(
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            documents_table=overrides.get("documents_table", SUPABASE_DOCS_TABLE),
            nodes_table=overrides.get("nodes_table", SUPABASE_NODES_TABLE),
            edges_table=overrides.get("edges_table", SUPABASE_EDGES_TABLE),
            episodes_table=overrides.get("episodes_table", "kg_episodes"),
            embeddings_model=overrides.get("embeddings_model", OPENAI_EMBED_MODEL),
            store_raw_episode_content=overrides.get(
                "store_raw_episode_content", True  # type: ignore[arg-type]
            ),
        )


@dataclass(slots=True)
class RetrievalConfig:
    supabase_url: str
    supabase_key: str
    documents_table: str = SUPABASE_DOCS_TABLE
    nodes_table: str = SUPABASE_NODES_TABLE
    edges_table: str = SUPABASE_EDGES_TABLE
    match_documents_fn: str = "match_documents"
    max_context_documents: int = 12
    max_neighbor_hops: int = 2
    match_threshold: float = 0.2
    cross_encoder_model: Optional[str] = None
    query_embedding_model: str = OPENAI_EMBED_MODEL
    openai_api_key: Optional[str] = field(
        default_factory=lambda: OPENAI_API_KEY if OPENAI_API_KEY else None
    )

    @classmethod
    def from_env(cls, **overrides: str) -> "RetrievalConfig":
        supabase_url = overrides.get("supabase_url") or SUPABASE_URL
        supabase_key = overrides.get("supabase_key") or SUPABASE_KEY

        if not supabase_url or not supabase_key:
            raise ConfigurationError(
                "SUPABASE_URL and SUPABASE_KEY must be configured before using the hybrid retrieval service."
            )

        return cls(
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            documents_table=overrides.get("documents_table", SUPABASE_DOCS_TABLE),
            nodes_table=overrides.get("nodes_table", SUPABASE_NODES_TABLE),
            edges_table=overrides.get("edges_table", SUPABASE_EDGES_TABLE),
            match_documents_fn=overrides.get("match_documents_fn", "match_documents"),
            max_context_documents=int(overrides.get("max_context_documents", 12)),
            max_neighbor_hops=int(overrides.get("max_neighbor_hops", 2)),
            match_threshold=float(overrides.get("match_threshold", 0.2)),
            cross_encoder_model=overrides.get("cross_encoder_model"),
            query_embedding_model=overrides.get("query_embedding_model", OPENAI_EMBED_MODEL),
            openai_api_key=overrides.get("openai_api_key") or OPENAI_API_KEY or None,
        )


def ensure_openai_key() -> str:
    api_key = OPENAI_API_KEY
    if not api_key:
        raise ConfigurationError("OPENAI_API_KEY is required for embedding/reranking.")
    return api_key
