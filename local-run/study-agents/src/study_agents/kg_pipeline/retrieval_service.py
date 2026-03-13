from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from openai import OpenAI
from .config import IngestionConfig, RetrievalConfig, ensure_openai_key
from .models import (
    HybridRetrievalResult,
    HybridRetrievalTelemetry,
    RetrievalDocument,
)
from .supabase_adapter import SupabaseGraphStore

logger = logging.getLogger(__name__)


class HybridRetrievalService:
    """
    First pass of the upgraded retrieval layer. It still leans on the existing
    `match_documents` RPC, but it exposes explicit hooks for BM25, BFS-based
    graph expansion, and cross-encoder reranking so we can tighten the loop in
    subsequent commits.
    """

    def __init__(
        self,
        retrieval_config: RetrievalConfig | None = None,
        graph_store: SupabaseGraphStore | None = None,
        openai_client: OpenAI | None = None,
    ):
        self.config = retrieval_config or RetrievalConfig.from_env()
        ingestion_cfg = IngestionConfig.from_env(
            supabase_url=self.config.supabase_url,
            supabase_key=self.config.supabase_key,
            documents_table=self.config.documents_table,
            nodes_table=self.config.nodes_table,
            edges_table=self.config.edges_table,
        )
        self.graph_store = graph_store or SupabaseGraphStore(ingestion_cfg)
        self._openai_client = openai_client

    # ------------------------------------------------------------------ #
    def retrieve_context(
        self,
        question: str,
        match_count: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        include_graph: bool = True,
    ) -> HybridRetrievalResult:
        """
        Retrieve context for a question using semantic similarity first, then
        optionally expand into the graph. The method returns structured context
        plus telemetry so the caller can log/audit the run.
        """

        if not question.strip():
            raise ValueError("Question cannot be empty")

        start = time.perf_counter()
        match_count = match_count or self.config.max_context_documents

        documents = self._semantic_search(question, match_count)
        graph_nodes: list[dict[str, Any]] = []
        graph_edges: list[dict[str, Any]] = []
        if include_graph and documents:
            graph_nodes, graph_edges = self._expand_graph(documents)

        telemetry = HybridRetrievalTelemetry(
            documents_examined=len(documents),
            graph_hops=1 if graph_nodes else 0,
            reranker=self.config.cross_encoder_model,
            latency_ms=0.0,
        )

        telemetry.latency_ms = (time.perf_counter() - start) * 1000
        if not documents:
            telemetry.notes.append("semantic_search_returned_empty")

        return HybridRetrievalResult(
            documents=documents,
            graph_nodes=graph_nodes if include_graph else [],
            graph_edges=graph_edges if include_graph else [],
            telemetry=telemetry,
        )

    # ------------------------------------------------------------------ #
    def _semantic_search(self, question: str, match_count: int) -> List[RetrievalDocument]:
        query_embedding = self._embed_query(question)
        payload = {
            "query_embedding": query_embedding,
            "match_threshold": self.config.match_threshold,
            "match_count": match_count,
        }
        logger.debug(
            "Invoking Supabase RPC %s (match_count=%s)",
            self.config.match_documents_fn,
            match_count,
        )
        response = (
            self.graph_store.client.rpc(self.config.match_documents_fn, payload).execute()
        )
        rows = response.data or []
        return [
            RetrievalDocument(
                doc_id=row.get("id"),
                content=row.get("content"),
                similarity=row.get("similarity", 0.0),
                metadata=row.get("meta") or {},
            )
            for row in rows
        ]

    def _expand_graph(
        self, documents: List[RetrievalDocument], max_neighbors: int = 20
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Expand the graph around the sections referenced by retrieved documents.
        Uses targeted queries (by section_id) to avoid full-table scans.
        """
        section_ids: list[str] = []
        for doc in documents:
            section_id = doc.metadata.get("section_id")
            if section_id and section_id not in section_ids:
                section_ids.append(section_id)
            if len(section_ids) >= 10:
                break

        if not section_ids:
            return [], []

        edges: list[dict[str, Any]] = []
        nodes: dict[str, dict[str, Any]] = {}

        for sec_id in section_ids:
            try:
                edge_res = (
                    self.graph_store.client.table(self.config.edges_table)
                    .select("*")
                    .or_(f"src.eq.{sec_id},dst.eq.{sec_id}")
                    .limit(max_neighbors)
                    .execute()
                )
                for edge in edge_res.data or []:
                    edges.append(edge)
                    other_id = edge["dst"] if edge["src"] == sec_id else edge["src"]
                    if other_id not in nodes:
                        node_res = (
                            self.graph_store.client.table(self.config.nodes_table)
                            .select("*")
                            .eq("id", other_id)
                            .limit(1)
                            .execute()
                        )
                        if node_res.data:
                            nodes[other_id] = node_res.data[0]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Graph expansion failed for %s: %s", sec_id, exc)

        return list(nodes.values())[:max_neighbors], edges[:max_neighbors]

    def _embed_query(self, question: str) -> list[float]:
        client = self._openai_client
        if client is None:
            key = self.config.openai_api_key or ensure_openai_key()
            client = OpenAI(api_key=key)
            self._openai_client = client
        embedding = client.embeddings.create(
            model=self.config.query_embedding_model,
            input=question,
        )
        return embedding.data[0].embedding
