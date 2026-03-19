from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, List, Optional


@dataclass(slots=True)
class EpisodeChunk:
    """Atomic unit of content that can be embedded + indexed."""

    chunk_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None


@dataclass(slots=True)
class EpisodePayload:
    """Normalized payload for the ingestion service."""

    episode_id: str
    source: str
    source_type: str
    reference_time: datetime
    group_id: str
    profile_id: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    chunks: list[EpisodeChunk] = field(default_factory=list)
    raw_text: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphNodeCandidate:
    """Represents a potential node emitted by the extraction prompts."""

    node_id: str
    title: str
    node_type: str
    group_id: str
    profile_id: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphEdgeCandidate:
    """Represents a potential edge emitted by the extraction prompts."""

    src: str
    dst: str
    rel: str
    group_id: str
    profile_id: Optional[str] = None
    attrs: dict[str, Any] = field(default_factory=dict)
    valid_at: Optional[datetime] = None
    invalid_at: Optional[datetime] = None


@dataclass(slots=True)
class IngestionResult:
    documents_written: int = 0
    nodes_written: int = 0
    edges_written: int = 0
    episodes_written: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RetrievalDocument:
    doc_id: str
    content: str
    similarity: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HybridRetrievalTelemetry:
    documents_examined: int
    graph_hops: int
    reranker: Optional[str]
    latency_ms: float
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HybridRetrievalResult:
    documents: list[RetrievalDocument] = field(default_factory=list)
    graph_nodes: list[dict[str, Any]] = field(default_factory=list)
    graph_edges: list[dict[str, Any]] = field(default_factory=list)
    telemetry: Optional[HybridRetrievalTelemetry] = None
