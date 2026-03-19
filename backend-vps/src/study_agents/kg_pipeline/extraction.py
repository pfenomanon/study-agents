from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence

from openai import OpenAI

from ..config import MODEL_NAME, OLLAMA_API_KEY, OLLAMA_HOST, REASON_MODEL
from ..openai_compat import create_chat_completion
from ..prompt_loader import load_required_prompt
from .config import ensure_openai_key
from .models import EpisodeChunk, EpisodePayload, GraphEdgeCandidate, GraphNodeCandidate

logger = logging.getLogger(__name__)

KG_EXTRACTION_MODEL = (
    os.getenv("KG_EXTRACTION_MODEL")
    or os.getenv("SCHEMA_MODEL_NAME")
    or REASON_MODEL
    or MODEL_NAME
).strip()
KG_EXTRACTION_PLATFORM = (os.getenv("KG_EXTRACTION_PLATFORM") or "").strip().lower()
KG_EXTRACTION_OLLAMA_TARGET = (
    os.getenv("KG_EXTRACTION_OLLAMA_TARGET")
    or os.getenv("OLLAMA_TARGET")
    or "cloud"
).strip().lower()
KG_EXTRACTION_CONTEXT_CHAR_LIMIT = max(
    1200, int((os.getenv("KG_EXTRACTION_CONTEXT_CHAR_LIMIT") or "4500").strip())
)
KG_EXTRACTION_CONTEXT_CHUNK_LIMIT = max(
    1, int((os.getenv("KG_EXTRACTION_CONTEXT_CHUNK_LIMIT") or "10").strip())
)
KG_EXTRACTION_MIN_CONTEXT_CHARS = max(
    800, int((os.getenv("KG_EXTRACTION_MIN_CONTEXT_CHARS") or "1000").strip())
)


def _slugify(name: str) -> str:
    cleaned = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip()
    cleaned = cleaned.replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or f"node_{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class ExtractionResult:
    nodes: Sequence[GraphNodeCandidate]
    edges: Sequence[GraphEdgeCandidate]


class GraphExtractionEngine:
    """Runs lightweight Graphiti-style prompts to propose KG nodes + edges."""

    def __init__(self, openai_client: OpenAI | None = None):
        self.runtime = self._resolve_runtime()
        self.client: OpenAI | None = None
        if self.runtime["platform"] == "openai":
            self.client = openai_client or OpenAI(api_key=ensure_openai_key())
        self.entity_prompt = load_required_prompt("kg_entity_extraction.txt")
        self.edge_prompt = load_required_prompt("kg_edge_extraction.txt")
        logger.info(
            "KG extraction runtime resolved: platform=%s model=%s",
            self.runtime.get("platform"),
            self.runtime.get("model"),
        )

    def extract(self, payload: EpisodePayload) -> ExtractionResult:
        context = self._build_context(payload.chunks)
        if not context:
            return ExtractionResult(nodes=(), edges=())

        entities = self._run_entity_prompt(context)
        nodes = self._materialize_nodes(
            entities, payload.group_id, payload.profile_id
        )
        edges = self._materialize_edges(
            context, nodes, payload.group_id, payload.profile_id
        )
        return ExtractionResult(nodes=nodes, edges=edges)

    def _build_context(
        self, chunks: Iterable[EpisodeChunk], limit: int = KG_EXTRACTION_CONTEXT_CHUNK_LIMIT
    ) -> str:
        texts = []
        for idx, chunk in enumerate(chunks):
            if idx >= limit:
                break
            texts.append(chunk.text.strip())
        combined = "\n\n".join(t for t in texts if t)
        return combined[:KG_EXTRACTION_CONTEXT_CHAR_LIMIT]

    @staticmethod
    def _is_context_window_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return (
            "maximum context length" in message
            or "context length" in message
            or "too many tokens" in message
            or "max token" in message
        )

    @staticmethod
    def _context_backoff_lengths(length: int, *, min_chars: int) -> list[int]:
        candidates = [
            length,
            int(length * 0.75),
            int(length * 0.55),
            int(length * 0.40),
            min_chars,
        ]
        deduped: list[int] = []
        for value in candidates:
            normalized = max(min_chars, value)
            if normalized not in deduped:
                deduped.append(normalized)
        return deduped

    def _run_entity_prompt(self, context: str) -> Sequence[dict]:
        data = None
        attempts = self._context_backoff_lengths(
            len(context), min_chars=KG_EXTRACTION_MIN_CONTEXT_CHARS
        )
        for max_chars in attempts:
            prompt = self.entity_prompt.format(context=context[:max_chars])
            try:
                content = self._run_response(prompt)
                data = self._parse_json(content)
                break
            except Exception as exc:  # pragma: no cover
                if self._is_context_window_error(exc) and max_chars > KG_EXTRACTION_MIN_CONTEXT_CHARS:
                    logger.warning(
                        "Entity extraction context overflow; retrying with smaller context (%s chars): %s",
                        max_chars,
                        exc,
                    )
                    continue
                logger.warning(
                    "Entity extraction failed (platform=%s model=%s): %s",
                    self.runtime.get("platform"),
                    self.runtime.get("model"),
                    exc,
                )
                return []
        if data is None:
            return []
        if isinstance(data, dict):
            return data.get("entities", [])
        if isinstance(data, list):
            return data
        logger.warning("Unexpected entity payload type: %s", type(data).__name__)
        return []

    def _run_edge_prompt(
        self, context: str, node_names: Sequence[str]
    ) -> Sequence[dict]:
        if not node_names:
            return []
        data = None
        context_attempts = self._context_backoff_lengths(
            len(context), min_chars=KG_EXTRACTION_MIN_CONTEXT_CHARS
        )
        entity_limits = (20, 15, 10, 8)
        for entity_limit in entity_limits:
            prefix = "Entities:\n" + "\n".join(f"- {name}" for name in node_names[:entity_limit])
            for max_chars in context_attempts:
                prompt = self.edge_prompt.format(context=f"{prefix}\n\n{context[:max_chars]}")
                try:
                    content = self._run_response(prompt)
                    data = self._parse_json(content)
                    break
                except Exception as exc:  # pragma: no cover
                    if self._is_context_window_error(exc) and max_chars > KG_EXTRACTION_MIN_CONTEXT_CHARS:
                        logger.warning(
                            "Edge extraction context overflow; retrying with smaller context (%s chars): %s",
                            max_chars,
                            exc,
                        )
                        continue
                    if self._is_context_window_error(exc):
                        logger.warning(
                            "Edge extraction still over context at minimum payload; retrying with fewer entities: %s",
                            exc,
                        )
                        break
                    logger.warning(
                        "Edge extraction failed (platform=%s model=%s): %s",
                        self.runtime.get("platform"),
                        self.runtime.get("model"),
                        exc,
                    )
                    return []
            if data is not None:
                break
        if data is None:
            return []
        if isinstance(data, dict):
            return data.get("relationships", [])
        if isinstance(data, list):
            return data
        logger.warning(
            "Unexpected relationship payload type: %s", type(data).__name__
        )
        return []

    def _materialize_nodes(
        self, raw_entities: Sequence[dict], group_id: str, profile_id: str | None
    ) -> Sequence[GraphNodeCandidate]:
        nodes: list[GraphNodeCandidate] = []
        seen_titles: set[str] = set()
        for entity in raw_entities:
            title = (entity.get("name") or "").strip()
            if not title:
                continue
            norm = title.lower()
            if norm in seen_titles:
                continue
            seen_titles.add(norm)
            node_suffix = _slugify(title)
            if profile_id:
                node_id = f"{entity.get('type', 'Node')}:{profile_id}:{node_suffix}"
            else:
                node_id = f"{entity.get('type', 'Node')}:{node_suffix}"
            nodes.append(
                GraphNodeCandidate(
                    node_id=node_id,
                    title=title,
                    node_type=entity.get("type") or "Concept",
                    group_id=group_id,
                    profile_id=profile_id,
                    attrs={"description": entity.get("description", "")},
                )
            )
        return nodes

    def _materialize_edges(
        self,
        context: str,
        nodes: Sequence[GraphNodeCandidate],
        group_id: str,
        profile_id: str | None,
    ) -> Sequence[GraphEdgeCandidate]:
        lookup = {node.title.lower(): node.node_id for node in nodes}
        raw_edges = self._run_edge_prompt(context, list(lookup.keys()))
        edges: list[GraphEdgeCandidate] = []
        for rel in raw_edges:
            src_title = rel.get("source")
            dst_title = rel.get("target")
            rel_type = rel.get("type")
            if not src_title or not dst_title or not rel_type:
                continue
            src_id = lookup.get(src_title.lower())
            dst_id = lookup.get(dst_title.lower())
            if not src_id or not dst_id:
                continue
            valid_at = rel.get("valid_at")
            parsed_valid = None
            if isinstance(valid_at, str):
                try:
                    parsed_valid = datetime.fromisoformat(valid_at.replace("Z", "+00:00"))
                except ValueError:
                    parsed_valid = None
            edges.append(
                GraphEdgeCandidate(
                    src=src_id,
                    dst=dst_id,
                    rel=rel_type,
                    group_id=group_id,
                    profile_id=profile_id,
                    attrs={"description": rel.get("description", "")},
                    valid_at=parsed_valid,
                )
            )
        return edges

    # ------------------------------------------------------------------ #
    # Model runtime helpers
    # ------------------------------------------------------------------ #
    def _resolve_runtime(self) -> dict[str, Optional[str]]:
        model = KG_EXTRACTION_MODEL or REASON_MODEL
        platform = KG_EXTRACTION_PLATFORM
        if not platform:
            platform = "ollama" if ":" in model else (os.getenv("REASON_PLATFORM", "openai") or "openai").strip().lower()
        if platform not in {"openai", "ollama"}:
            raise ValueError("KG extraction platform must be 'openai' or 'ollama'.")

        runtime: dict[str, Optional[str]] = {
            "platform": platform,
            "model": model,
            "ollama_host": None,
            "ollama_api_key": None,
        }
        if platform == "ollama":
            target = KG_EXTRACTION_OLLAMA_TARGET
            if target not in {"local", "cloud"}:
                target = "cloud"
            if target == "local":
                host = (os.getenv("OLLAMA_LOCAL_HOST") or "http://127.0.0.1:11434").strip()
                api_key = (os.getenv("OLLAMA_LOCAL_API_KEY") or "").strip() or None
            else:
                host = (os.getenv("OLLAMA_CLOUD_HOST") or OLLAMA_HOST or "").strip()
                api_key = (os.getenv("OLLAMA_CLOUD_API_KEY") or OLLAMA_API_KEY or "").strip() or None
            if not host:
                raise ValueError("Ollama host is not configured for KG extraction.")
            runtime["ollama_host"] = host
            runtime["ollama_api_key"] = api_key
        return runtime

    def _run_response(self, prompt: str) -> str:
        model = self.runtime.get("model") or KG_EXTRACTION_MODEL
        if self.runtime.get("platform") == "openai":
            if self.client is None:
                raise ValueError("OpenAI client is not initialized for KG extraction.")
            completion = create_chat_completion(
                self.client,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            return (completion.choices[0].message.content or "").strip()

        import ollama

        headers: dict[str, str] = {}
        api_key = self.runtime.get("ollama_api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        host = self.runtime.get("ollama_host")
        if not host:
            raise ValueError("Ollama host is not configured.")
        client = ollama.Client(host=host, headers=headers or None)
        result = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        if isinstance(result, dict):
            return str((result.get("message", {}) or {}).get("content", "")).strip()
        message = getattr(result, "message", None)
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
        if message is not None and hasattr(message, "get"):
            return str(message.get("content") or "").strip()
        return ""

    def _parse_json(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Failed to parse JSON, attempting fallback. Raw snippet: %s", raw[:200])
            match = re.search(r"\{.*\}", raw, flags=re.S)
            if not match:
                raise
            return json.loads(match.group())
