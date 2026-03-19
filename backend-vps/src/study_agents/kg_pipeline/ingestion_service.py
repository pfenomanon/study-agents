from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Sequence

from openai import OpenAI

from ..config import OPENAI_API_KEY
from .config import IngestionConfig, ensure_openai_key
from .extraction import GraphExtractionEngine
from .models import (
    EpisodeChunk,
    EpisodePayload,
    GraphEdgeCandidate,
    GraphNodeCandidate,
    IngestionResult,
)
from .supabase_adapter import SupabaseGraphStore

logger = logging.getLogger(__name__)


class KnowledgeIngestionService:
    """
    High-level orchestrator that converts an EpisodePayload into Supabase
    documents/nodes/edges using Graphiti-inspired prompts.

    The service is intentionally conservative right now—most of the heavy lifting
    (prompt orchestration, temporal invalidation) will land incrementally. This
    scaffolding simply provides the hooks and logging we need to iterate safely.
    """

    def __init__(
        self,
        ingestion_config: IngestionConfig | None = None,
        graph_store: SupabaseGraphStore | None = None,
        openai_client: OpenAI | None = None,
    ):
        self.config = ingestion_config or IngestionConfig.from_env()
        self.graph_store = graph_store or SupabaseGraphStore(self.config)
        self._openai_client = openai_client
        self._extraction_engine = GraphExtractionEngine(openai_client=self._openai_client)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def ingest_episode(self, payload: EpisodePayload) -> IngestionResult:
        """
        Persist the supplied episode into Supabase.

        Steps (current iteration):
        1. Ensure every chunk has an embedding.
        2. Upsert chunk rows into the documents table.
        3. Run placeholder extraction to produce node/edge candidates.
        4. Record an episode stub for lineage.
        """

        if not payload.chunks:
            raise ValueError("EpisodePayload.chunks cannot be empty")

        logger.info(
            "Ingesting episode %s (%s chunks) into group %s",
            payload.episode_id,
            len(payload.chunks),
            payload.group_id,
        )

        chunks = self._ensure_embeddings(payload.chunks)
        documents_written = self.graph_store.upsert_documents(
            chunks, payload.group_id, payload.profile_id
        )

        extraction = self._extraction_engine.extract(payload)
        nodes_written = self.graph_store.upsert_nodes(extraction.nodes)
        edges_written = self.graph_store.upsert_edges(
            extraction.edges, payload.group_id, payload.profile_id
        )

        warnings: list[str] = []
        episodes_written = 0
        if nodes_written or edges_written:
            self._record_episode(payload)
            episodes_written = 1
        else:
            warnings.append(
                "Graph extraction produced zero nodes/edges; episode log skipped."
            )

        return IngestionResult(
            documents_written=documents_written,
            nodes_written=nodes_written,
            edges_written=edges_written,
            episodes_written=episodes_written,
            warnings=warnings,
        )

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _ensure_embeddings(self, chunks: Iterable[EpisodeChunk]) -> list[EpisodeChunk]:
        result: list[EpisodeChunk] = []
        client = None
        for chunk in chunks:
            if chunk.embedding is None:
                if client is None:
                    client = self._openai_client or OpenAI(api_key=ensure_openai_key())
                embedding = (
                    client.embeddings.create(
                        model=self.config.embeddings_model,
                        input=chunk.text,
                    )
                    .data[0]
                    .embedding
                )
                chunk.embedding = embedding
            result.append(chunk)
        return result

    def _extract_graph_candidates(
        self, payload: EpisodePayload
    ) -> tuple[Sequence[GraphNodeCandidate], Sequence[GraphEdgeCandidate]]:
        """
        Placeholder for the Graphiti-style prompt pipeline.

        For now we simply log the intent and return empty collections so that
        downstream callers have consistent return values. Future iterations will
        plug in actual LLM calls that produce typed nodes + edges.
        """

        logger.debug(
            "Graph extraction placeholder invoked for episode %s (source=%s)",
            payload.episode_id,
            payload.source,
        )
        return (), ()

    def _record_episode(self, payload: EpisodePayload) -> None:
        if not self.config.episodes_table:
            return
        record = {
            "episode_id": payload.episode_id,
            "source": payload.source,
            "source_type": payload.source_type,
            "group_id": payload.group_id,
            "profile_id": payload.profile_id,
            "tags": payload.tags,
            "reference_time": payload.reference_time.isoformat(),
            "metadata": payload.metadata,
        }
        if self.config.store_raw_episode_content and payload.raw_text:
            record["raw_content"] = payload.raw_text
        self.graph_store.record_episode(record)
