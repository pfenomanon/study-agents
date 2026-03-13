from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List

from .models import EpisodeChunk, EpisodePayload


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip()
    cleaned = cleaned.replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or f"doc_{uuid.uuid4().hex[:8]}"


def _load_chunks(chunks_path: Path) -> list[EpisodeChunk]:
    chunks: list[EpisodeChunk] = []
    with chunks_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            chunk_id = data.get("id") or f"chunk_{uuid.uuid4().hex}"
            text = data.get("text") or ""
            metadata = {k: v for k, v in data.items() if k not in {"id", "text"}}
            chunks.append(EpisodeChunk(chunk_id=chunk_id, text=text, metadata=metadata))
    return chunks


def aggregate_tags(chunks: Iterable[EpisodeChunk]) -> list[str]:
    tags: set[str] = set()
    for chunk in chunks:
        chunk_tags = chunk.metadata.get("tags")
        if isinstance(chunk_tags, list):
            tags.update(tag for tag in chunk_tags if isinstance(tag, str))
    return sorted(tags)


def episode_from_rag_artifacts(
    pdf_path: Path,
    artifacts: Dict[str, str],
    *,
    group_id: str | None = None,
    reference_time: datetime | None = None,
) -> EpisodePayload:
    pdf_path = Path(pdf_path)
    artifacts_folder = Path(artifacts["folder"])
    chunks_path = Path(artifacts["chunks"])
    markdown_path = Path(artifacts.get("markdown", ""))

    chunks = _load_chunks(chunks_path)
    slug = group_id or _slugify(pdf_path.stem)
    ref_time = reference_time or datetime.fromtimestamp(pdf_path.stat().st_mtime)

    raw_text: str | None = None
    if markdown_path.exists():
        try:
            raw_text = markdown_path.read_text(encoding="utf-8")
        except Exception:
            raw_text = None

    payload = EpisodePayload(
        episode_id=f"EP:{slug}:{uuid.uuid4().hex[:8]}",
        source=str(pdf_path.name),
        source_type="pdf",
        reference_time=ref_time,
        group_id=slug,
        tags=aggregate_tags(chunks),
        chunks=chunks,
        raw_text=raw_text,
        metadata={
            "source_path": str(pdf_path),
            "artifact_folder": str(artifacts_folder),
            "analysis": artifacts.get("analysis"),
            "markdown": artifacts.get("markdown"),
        },
    )
    return payload


__all__ = ["episode_from_rag_artifacts"]
