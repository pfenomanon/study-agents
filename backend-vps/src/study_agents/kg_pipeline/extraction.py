from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from openai import OpenAI

from ..prompt_loader import load_prompt
from .config import ensure_openai_key
from .models import EpisodeChunk, EpisodePayload, GraphEdgeCandidate, GraphNodeCandidate

logger = logging.getLogger(__name__)

_DEFAULT_ENTITY_PROMPT = (
    "You are Graphiti, a temporal knowledge-graph builder for regulated industries.\n"
    "Given the CONTEXT below, extract up to 20 entities...\n"
)
_DEFAULT_EDGE_PROMPT = (
    "You are Graphiti, a temporal knowledge-graph builder for regulated industries.\n"
    "Using only the facts in CONTEXT, derive relationships...\n"
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
        self.client = openai_client or OpenAI(api_key=ensure_openai_key())
        self.entity_prompt = load_prompt(
            "kg_entity_extraction.txt", _DEFAULT_ENTITY_PROMPT
        )
        self.edge_prompt = load_prompt("kg_edge_extraction.txt", _DEFAULT_EDGE_PROMPT)

    def extract(self, payload: EpisodePayload) -> ExtractionResult:
        context = self._build_context(payload.chunks)
        if not context:
            return ExtractionResult(nodes=(), edges=())

        entities = self._run_entity_prompt(context)
        nodes = self._materialize_nodes(entities, payload.group_id)
        edges = self._materialize_edges(context, nodes, payload.group_id)
        return ExtractionResult(nodes=nodes, edges=edges)

    def _build_context(self, chunks: Iterable[EpisodeChunk], limit: int = 20) -> str:
        texts = []
        for idx, chunk in enumerate(chunks):
            if idx >= limit:
                break
            texts.append(chunk.text.strip())
        combined = "\n\n".join(t for t in texts if t)
        return combined[:12000]

    def _run_entity_prompt(self, context: str) -> Sequence[dict]:
        prompt = self.entity_prompt.format(context=context)
        try:
            content = self._run_response(prompt)
            data = self._parse_json(content)
        except Exception as exc:  # pragma: no cover
            logger.warning("Entity extraction failed: %s", exc)
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
        prefix = "Entities:\n" + "\n".join(f"- {name}" for name in node_names[:20])
        prompt = self.edge_prompt.format(context=f"{prefix}\n\n{context}")
        try:
            content = self._run_response(prompt)
            data = self._parse_json(content)
        except Exception as exc:  # pragma: no cover
            logger.warning("Edge extraction failed: %s", exc)
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
        self, raw_entities: Sequence[dict], group_id: str
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
            node_id = f"{entity.get('type', 'Node')}:{_slugify(title)}"
            nodes.append(
                GraphNodeCandidate(
                    node_id=node_id,
                    title=title,
                    node_type=entity.get("type") or "Concept",
                    group_id=group_id,
                    attrs={"description": entity.get("description", "")},
                )
            )
        return nodes

    def _materialize_edges(
        self,
        context: str,
        nodes: Sequence[GraphNodeCandidate],
        group_id: str,
    ) -> Sequence[GraphEdgeCandidate]:
        lookup = {node.title.lower(): node.node_id for node in nodes}
        title_map = {node.title.lower(): node.title for node in nodes}
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
                    attrs={"description": rel.get("description", "")},
                    valid_at=parsed_valid,
                )
            )
        return edges

    # ------------------------------------------------------------------ #
    # OpenAI helpers
    # ------------------------------------------------------------------ #
    def _run_response(self, prompt: str) -> str:
        completion = self.client.responses.create(
            model="gpt-4o-mini",
            input=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return completion.output[0].content[0].text  # type: ignore[attr-defined]

    def _parse_json(self, raw: str) -> dict:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Failed to parse JSON, attempting fallback. Raw snippet: %s", raw[:200])
            match = re.search(r"\{.*\}", raw, flags=re.S)
            if not match:
                raise
            return json.loads(match.group())
