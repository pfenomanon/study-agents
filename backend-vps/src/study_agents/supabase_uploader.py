"""Helpers to push RAG artifacts into Supabase tables."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from openai import OpenAI

from .config import (
    OPENAI_API_KEY,
    OPENAI_EMBED_MODEL,
    SUPABASE_DOCS_TABLE,
    SUPABASE_EDGES_TABLE,
    SUPABASE_KEY,
    SUPABASE_NODES_TABLE,
    SUPABASE_URL,
)
from .supabase_client import create_supabase_client


def _require_supabase_client():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL/SUPABASE_KEY must be set in the environment")
    return create_supabase_client()


def _require_openai_client():
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY must be set to push embeddings to Supabase")
    return OpenAI(api_key=OPENAI_API_KEY)


def _iter_jsonl(path: Path) -> Iterable[dict]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def push_documents(chunks_path: Path) -> int:
    supabase = _require_supabase_client()
    openai_client = _require_openai_client()
    chunks = list(_iter_jsonl(chunks_path))
    count = 0
    batch_size = 16
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        try:
            embeddings = openai_client.embeddings.create(
                model=OPENAI_EMBED_MODEL,
                input=[c["text"] for c in batch],
            ).data
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Embedding generation failed: {exc}") from exc

        rows = []
        for chunk, emb in zip(batch, embeddings):
            rows.append(
                {
                    "id": chunk["id"],
                    "content": chunk["text"],
                    "meta": {
                        "section_id": chunk.get("section_id"),
                        "page_start": chunk.get("page_start"),
                        "page_end": chunk.get("page_end"),
                        "tags": chunk.get("tags"),
                    },
                    "embedding": emb.embedding,
                }
            )
        supabase.table(SUPABASE_DOCS_TABLE).upsert(rows).execute()
        count += len(rows)
    return count


def push_nodes(nodes_path: Path) -> int:
    supabase = _require_supabase_client()
    count = 0
    for node in _iter_jsonl(nodes_path):
        supabase.table(SUPABASE_NODES_TABLE).upsert(node).execute()
        count += 1
    return count


def push_edges(edges_path: Path) -> int:
    supabase = _require_supabase_client()
    count = 0
    for edge in _iter_jsonl(edges_path):
        supabase.table(SUPABASE_EDGES_TABLE).insert(edge).execute()
        count += 1
    return count


def push_bundle(artifacts: dict) -> dict:
    chunks = Path(artifacts["chunks"])
    nodes = Path(artifacts["nodes"])
    edges = Path(artifacts["edges"])
    return {
        "documents": push_documents(chunks),
        "nodes": push_nodes(nodes),
        "edges": push_edges(edges),
    }


__all__ = ["push_bundle", "push_documents", "push_nodes", "push_edges"]
