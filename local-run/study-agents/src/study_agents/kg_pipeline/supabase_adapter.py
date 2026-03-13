from __future__ import annotations

import logging
from typing import Iterable, Sequence

from supabase import Client

from .config import IngestionConfig
from .models import EpisodeChunk, GraphEdgeCandidate, GraphNodeCandidate
from ..supabase_client import create_supabase_client

logger = logging.getLogger(__name__)


class SupabaseGraphStore:
    """
    Light-weight wrapper around the Supabase client that centralises table names,
    batching, and logging.

    The goal is to keep all low-level persistence code in one place so the
    ingestion and retrieval services can focus on orchestration.
    """

    def __init__(self, config: IngestionConfig, client: Client | None = None):
        self.config = config
        self.client = client or create_supabase_client(
            url=config.supabase_url, key=config.supabase_key
        )
        self._edges_support_group = True

    # ------------------------------------------------------------------ #
    # Document helpers
    # ------------------------------------------------------------------ #
    def upsert_documents(self, chunks: Iterable[EpisodeChunk]) -> int:
        records = []
        for chunk in chunks:
            record = {
                "id": chunk.chunk_id,
                "content": chunk.text,
                "meta": chunk.metadata,
                "embedding": chunk.embedding,
            }
            records.append(record)

        if not records:
            return 0

        logger.debug(
            "Upserting %s documents into %s", len(records), self.config.documents_table
        )
        self.client.table(self.config.documents_table).upsert(records).execute()
        return len(records)

    # ------------------------------------------------------------------ #
    # Graph helpers
    # ------------------------------------------------------------------ #
    def upsert_nodes(self, nodes: Sequence[GraphNodeCandidate]) -> int:
        inserted = 0
        for node in nodes:
            if self._find_node(node.group_id, node.title):
                continue
            record = {
                "id": node.node_id,
                "title": node.title,
                "type": node.node_type,
                "group_id": node.group_id,
                "attrs": node.attrs,
            }
            self.client.table(self.config.nodes_table).upsert(record).execute()
            inserted += 1
        if inserted:
            logger.debug("Inserted %s new nodes", inserted)
        return inserted

    def upsert_edges(self, edges: Sequence[GraphEdgeCandidate], group_id: str) -> int:
        inserted = 0
        for edge in edges:
            if self._edge_exists(edge.src, edge.dst, edge.rel):
                continue
            record = {
                "src": edge.src,
                "dst": edge.dst,
                "rel": edge.rel,
                "attrs": edge.attrs,
                "valid_at": edge.valid_at.isoformat() if edge.valid_at else None,
                "invalid_at": edge.invalid_at.isoformat() if edge.invalid_at else None,
            }
            if self._edges_support_group:
                record["group_id"] = group_id
            try:
                self.client.table(self.config.edges_table).insert(record).execute()
            except Exception as exc:
                message = str(exc)
                if (
                    self._edges_support_group
                    and "group_id" in message
                    and "schema cache" in message
                ):
                    logger.warning(
                        "kg_edges.group_id column not found; retrying without it. "
                        "Run the latest supabase_schema.sql to add the column permanently."
                    )
                    self._edges_support_group = False
                    record.pop("group_id", None)
                    self.client.table(self.config.edges_table).insert(record).execute()
                else:
                    raise
            inserted += 1
        if inserted:
            logger.debug("Inserted %s new edges", inserted)
        return inserted

    # ------------------------------------------------------------------ #
    # Episode helpers
    # ------------------------------------------------------------------ #
    def record_episode(self, payload: dict) -> None:
        if not self.config.episodes_table:
            logger.debug("episodes_table not configured; skipping episode log")
            return
        logger.debug("Recording episode %s", payload.get("episode_id"))
        self.client.table(self.config.episodes_table).upsert(payload).execute()

    # ------------------------------------------------------------------ #
    # Lookups / dedupe helpers
    # ------------------------------------------------------------------ #
    def _find_node(self, group_id: str, title: str) -> dict | None:
        try:
            response = (
                self.client.table(self.config.nodes_table)
                .select("id")
                .eq("group_id", group_id)
                .ilike("title", title)
                .maybe_single()
            )
            return response.data  # type: ignore[attr-defined]
        except Exception:
            return None

    def _node_exists(self, node_id: str) -> bool:
        try:
            response = (
                self.client.table(self.config.nodes_table)
                .select("id")
                .eq("id", node_id)
                .maybe_single()
            )
            return bool(response.data)  # type: ignore[attr-defined]
        except Exception:
            return False

    def _edge_exists(self, src: str, dst: str, rel: str) -> bool:
        try:
            response = (
                self.client.table(self.config.edges_table)
                .select("id")
                .eq("src", src)
                .eq("dst", dst)
                .eq("rel", rel)
                .maybe_single()
            )
            return bool(response.data)  # type: ignore[attr-defined]
        except Exception:
            return False
