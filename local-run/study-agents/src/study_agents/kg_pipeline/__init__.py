"""
Scaffolding for the revamped knowledge graph ingestion/retrieval pipeline.

The concrete logic will evolve over multiple iterations. This module simply
exposes the core building blocks so other packages can start importing them
without having to reach into private files.
"""

from .config import IngestionConfig, RetrievalConfig
from .artifact_ingestion import episode_from_rag_artifacts
from .ingestion_service import KnowledgeIngestionService
from .models import (
    EpisodeChunk,
    EpisodePayload,
    GraphEdgeCandidate,
    GraphNodeCandidate,
    HybridRetrievalResult,
    HybridRetrievalTelemetry,
    IngestionResult,
    RetrievalDocument,
)
from .retrieval_service import HybridRetrievalService
from .supabase_adapter import SupabaseGraphStore

__all__ = [
    "EpisodeChunk",
    "EpisodePayload",
    "GraphEdgeCandidate",
    "GraphNodeCandidate",
    "HybridRetrievalResult",
    "HybridRetrievalService",
    "HybridRetrievalTelemetry",
    "IngestionConfig",
    "IngestionResult",
    "KnowledgeIngestionService",
    "RetrievalConfig",
    "RetrievalDocument",
    "SupabaseGraphStore",
    "episode_from_rag_artifacts",
]
