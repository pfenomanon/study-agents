"""
Context-Aware Grouping (CAG) Agent

Enhances RAG with semantic clustering, knowledge graphs, and context-aware retrieval.
Uses Graphiti MCP for knowledge graph operations and integrates with Supabase.
"""

import argparse
import contextlib
import io
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Iterable
from dataclasses import dataclass

import numpy as np
from openai import OpenAI
import fitz  # PyMuPDF for PDF reading

from .config import (
    OPENAI_API_KEY,
    SUPABASE_URL,
    SUPABASE_KEY,
    SUPABASE_DOCS_TABLE,
    SUPABASE_NODES_TABLE,
    SUPABASE_EDGES_TABLE,
    OPENAI_EMBED_MODEL,
    USE_HYBRID_RETRIEVAL,
    REASON_MODEL,
    OLLAMA_HOST,
    OLLAMA_API_KEY,
)
from .supabase_client import create_supabase_client
from .rag_builder_core import chunk_text, split_into_paragraphs, guess_headings, slugify
from .openai_compat import create_chat_completion
from .prompt_loader import load_required_prompt
from .kg_pipeline import (
    HybridRetrievalResult,
    HybridRetrievalService,
    RetrievalConfig,
    EpisodeChunk,
    EpisodePayload,
    KnowledgeIngestionService,
)
from .profile_namespace import compose_group_id, normalize_profile_id, safe_doc_slug
from .settings import get_settings, SettingsError

logger = logging.getLogger(__name__)


ENTITY_PROMPT = load_required_prompt("cag_entity_extraction.txt")
RELATION_PROMPT = load_required_prompt("cag_relationship_extraction.txt")
ANSWER_PROMPT = load_required_prompt("cag_answer_generation.txt")
CLUSTER_PROMPT = load_required_prompt("cag_cluster_topic.txt")
GROUNDED_VERIFIER_PROMPT = load_required_prompt("cag_grounding_verifier_system.txt")
GROUNDED_REPAIR_PROMPT = load_required_prompt("cag_grounding_repair_system.txt")


@dataclass
class KnowledgeNode:
    """Represents a node in the knowledge graph."""
    id: str
    type: str  # entity, concept, topic, etc.
    label: str
    description: str
    embedding: Optional[List[float]] = None
    metadata: Optional[Dict] = None
    created_at: str = None


@dataclass
class KnowledgeEdge:
    """Represents a relationship between nodes."""
    id: str
    source_id: str
    target_id: str
    relationship: str  # influences, part_of, related_to, etc.
    weight: float = 1.0
    metadata: Optional[Dict] = None
    created_at: str = None


@dataclass
class SemanticCluster:
    """Represents a cluster of semantically related content."""
    id: str
    centroid: List[float]
    chunks: List[str]
    topic: str
    confidence: float


class CAGAgent:
    """
    Context-Aware Grouping Agent
    
    Enhances RAG with semantic clustering, knowledge graphs, and context-aware retrieval.
    """
    
    MAX_LABEL_LENGTH = 60
    ALLOWED_RELATIONSHIPS = {"contains", "defines", "related_to", "part_of", "influences"}
    _TRUE_VALUES = {"1", "true", "yes", "on"}

    def __init__(
        self,
        *,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        self._supabase = None  # lazy init to avoid import-time failures
        self._openai_client = None
        self.embedding_model = OPENAI_EMBED_MODEL
        self.use_hybrid_retrieval = USE_HYBRID_RETRIEVAL
        self._hybrid_retriever: Optional[HybridRetrievalService] = None
        self._ingestion_service: Optional[KnowledgeIngestionService] = None
        self._supabase_url = supabase_url
        self._supabase_key = supabase_key

    @property
    def supabase(self):
        if self._supabase is None:
            self._supabase = create_supabase_client(
                url=self._supabase_url,
                key=self._supabase_key,
            )
        return self._supabase

    @property
    def openai_client(self):
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)
        return self._openai_client

    def _get_ingestion_service(self) -> KnowledgeIngestionService:
        if self._ingestion_service is None:
            if self._supabase_url and self._supabase_key:
                from .kg_pipeline import IngestionConfig

                ingestion_config = IngestionConfig.from_env(
                    supabase_url=self._supabase_url,
                    supabase_key=self._supabase_key,
                )
                self._ingestion_service = KnowledgeIngestionService(
                    ingestion_config=ingestion_config
                )
            else:
                self._ingestion_service = KnowledgeIngestionService()
        return self._ingestion_service

    def _sanitize_label(self, label: str) -> str:
        cleaned = (label or "").strip()
        if not cleaned:
            return "Untitled"
        if len(cleaned) > self.MAX_LABEL_LENGTH:
            cleaned = cleaned[: self.MAX_LABEL_LENGTH - 3].rstrip() + "..."
        return cleaned

    def _sanitize_relationship(self, relationship: str) -> str:
        rel = (relationship or "").strip().lower()
        if rel in self.ALLOWED_RELATIONSHIPS:
            return rel
        return "related_to"

    def _strict_grounded_mode(self) -> bool:
        return (os.getenv("STRICT_GROUNDED_MODE", "true") or "true").strip().lower() in self._TRUE_VALUES

    def _extract_context_citation_ids(self, context: str) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        for match in re.findall(r"\[Document id=([^\]\s]+)", context or ""):
            cid = match.strip()
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        return ids

    def _parse_citations(self, answer: str) -> list[str]:
        citation_line = ""
        for line in (answer or "").splitlines():
            if line.strip().lower().startswith("citations:"):
                citation_line = line.split(":", 1)[1].strip()
                break
        if not citation_line:
            return []
        parts = [p.strip() for p in re.split(r"[,\s]+", citation_line) if p.strip()]
        return parts

    def _is_abstention(self, answer: str) -> bool:
        lowered = (answer or "").lower()
        markers = (
            "insufficient grounded evidence",
            "cannot determine from provided context",
            "unable to determine from provided context",
            "insufficient context",
        )
        return any(marker in lowered for marker in markers)

    def _abstain_response(self, reason: str) -> str:
        return (
            "Answer: Insufficient grounded evidence to answer from provided context.\n"
            f"Rationale: {reason}\n"
            "Citations: NONE"
        )

    def _has_required_sections(self, answer: str) -> bool:
        lowered = (answer or "").lower()
        return (
            "answer:" in lowered
            and "rationale:" in lowered
            and "citations:" in lowered
        )

    def _citations_valid(self, answer: str, allowed_ids: set[str]) -> bool:
        citations = self._parse_citations(answer)
        if not citations:
            return False
        if len(citations) == 1 and citations[0].upper() == "NONE":
            return self._is_abstention(answer)
        if not allowed_ids:
            return False
        for citation in citations:
            if citation not in allowed_ids:
                return False
        return True

    def _fallback_repair_citations(self, answer: str, allowed_ids: set[str]) -> str:
        """
        Deterministically repair/append citations using retrieved document ids.

        This is a last-resort guard for models that answer correctly but emit
        malformed or missing citation lines.
        """
        if not answer:
            return answer
        if not allowed_ids:
            return answer

        chosen = ", ".join(sorted(allowed_ids)[:2])
        lines = (answer or "").splitlines()
        rebuilt: list[str] = []
        has_answer = False
        has_rationale = False
        has_citations = False

        in_citations_block = False
        for raw in lines:
            line = raw.rstrip()
            lowered = line.strip().lower()
            if in_citations_block:
                if lowered.startswith("answer:") or lowered.startswith("rationale:"):
                    in_citations_block = False
                else:
                    continue
            if lowered.startswith("answer:"):
                has_answer = True
                rebuilt.append(line)
                continue
            if lowered.startswith("rationale:"):
                has_rationale = True
                rebuilt.append(line)
                continue
            if lowered.startswith("citations:"):
                has_citations = True
                rebuilt.append(f"Citations: {chosen}")
                in_citations_block = True
                continue
            rebuilt.append(line)

        if not has_answer:
            summary = " ".join((answer or "").split())
            rebuilt.insert(0, f"Answer: {summary}")
        if not has_rationale:
            rebuilt.append("Rationale: Derived from retrieved context with citation normalization.")
        if not has_citations:
            rebuilt.append(f"Citations: {chosen}")

        return "\n".join(rebuilt).strip()

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        raw = (text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _chat_completion_text(
        self,
        *,
        messages: list[dict[str, str]],
        runtime: Dict[str, Optional[str]],
        temperature: float = 0.2,
    ) -> str:
        platform = runtime.get("platform") or "openai"
        model = runtime.get("model") or REASON_MODEL
        if platform == "openai":
            response = create_chat_completion(
                self.openai_client,
                model=model,
                messages=messages,
                temperature=temperature,
            )
            return (response.choices[0].message.content or "").strip()

        import ollama

        headers: Dict[str, str] = {}
        api_key = runtime.get("ollama_api_key")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        host = runtime.get("ollama_host")
        if not host:
            raise ValueError("Ollama host is not configured.")

        client = ollama.Client(host=host, headers=headers or None)
        result = client.chat(
            model=model,
            messages=messages,
            options={"temperature": temperature},
        )
        if isinstance(result, dict):
            return (result.get("message", {}) or {}).get("content", "").strip()
        message = getattr(result, "message", None)
        if isinstance(message, dict):
            return (message.get("content") or "").strip()
        if message is not None and hasattr(message, "get"):
            return (message.get("content") or "").strip()
        return ""

    def _grounding_supported(
        self,
        *,
        question: str,
        context: str,
        answer: str,
        runtime: Dict[str, Optional[str]],
    ) -> tuple[bool, str]:
        verifier_runtime = self._verifier_runtime(runtime)
        verifier_messages = [
            {
                "role": "system",
                "content": GROUNDED_VERIFIER_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"QUESTION:\n{question}\n\n"
                    f"CONTEXT:\n{context[:4000]}\n\n"
                    f"ANSWER:\n{answer}\n"
                ),
            },
        ]
        verifier_raw = self._chat_completion_text(
            messages=verifier_messages,
            runtime=verifier_runtime,
            temperature=0.0,
        )
        parsed = self._extract_json_object(verifier_raw)
        supported = bool(parsed.get("supported"))
        reason = str(parsed.get("reason") or "Unsupported by retrieved context.")
        unsupported_claims = parsed.get("unsupported_claims") or []
        if isinstance(unsupported_claims, list) and unsupported_claims:
            reason = f"{reason} Unsupported claims: {', '.join(str(c) for c in unsupported_claims[:3])}"
        return supported, reason

    def _repair_grounded_answer_format(
        self,
        *,
        question: str,
        context: str,
        draft_answer: str,
        allowed_ids: set[str],
        runtime: Dict[str, Optional[str]],
    ) -> str:
        repair_runtime = self._verifier_runtime(runtime)
        allowed = ", ".join(sorted(allowed_ids)) or "NONE"
        repair_messages = [
            {
                "role": "system",
                "content": GROUNDED_REPAIR_PROMPT.replace("{allowed_ids}", allowed),
            },
            {
                "role": "user",
                "content": (
                    f"ALLOWED_CITATION_IDS:\n{allowed}\n\n"
                    f"QUESTION:\n{question}\n\n"
                    f"CONTEXT:\n{context[:4000]}\n\n"
                    f"DRAFT_ANSWER:\n{draft_answer}\n"
                ),
            },
        ]
        return self._chat_completion_text(
            messages=repair_messages,
            runtime=repair_runtime,
            temperature=0.0,
        )

    def _verifier_runtime(
        self, generation_runtime: Dict[str, Optional[str]]
    ) -> Dict[str, Optional[str]]:
        platform = (
            os.getenv("STRICT_GROUNDED_VERIFIER_PLATFORM", "openai").strip().lower()
        )
        model = (os.getenv("STRICT_GROUNDED_VERIFIER_MODEL", "o3-mini") or "o3-mini").strip()
        if platform == "openai":
            return {
                "platform": "openai",
                "model": model,
                "ollama_target": None,
                "ollama_host": None,
                "ollama_api_key": None,
            }
        if platform == "ollama":
            resolved = self.resolve_reasoning_runtime(platform="ollama", model=model)
            return {
                "platform": "ollama",
                "model": resolved.get("model"),
                "ollama_target": resolved.get("ollama_target"),
                "ollama_host": resolved.get("ollama_host"),
                "ollama_api_key": resolved.get("ollama_api_key"),
            }
        # Fallback: use generation runtime if misconfigured.
        return generation_runtime

    def _build_hierarchy(self, text: str, source: str) -> Tuple[List[KnowledgeNode], List[KnowledgeEdge]]:
        nodes: List[KnowledgeNode] = []
        edges: List[KnowledgeEdge] = []

        doc_label = self._sanitize_label(os.path.basename(source) if source else "Document")
        doc_id = f"DOC:{uuid.uuid4().hex}"
        timestamp = datetime.now().isoformat()
        doc_node = KnowledgeNode(
            id=doc_id,
            type="Document",
            label=doc_label,
            description=f"Document node sourced from {source or 'raw text'}",
            created_at=timestamp,
        )
        nodes.append(doc_node)

        paragraphs = split_into_paragraphs(text)
        headings = guess_headings(paragraphs)
        if not headings:
            headings = [p for p in paragraphs[:3] if p]
        for idx, heading in enumerate(headings[:5], start=1):
            section_label = self._sanitize_label(heading or f"Section {idx}")
            sec_id = f"SEC:{uuid.uuid4().hex}"
            sec_node = KnowledgeNode(
                id=sec_id,
                type="Section",
                label=section_label,
                description=f"Section extracted from document: {heading[:80]}",
                created_at=timestamp,
            )
            nodes.append(sec_node)
            edges.append(
                KnowledgeEdge(
                    id=f"EDGE:{uuid.uuid4().hex}",
                    source_id=doc_id,
                    target_id=sec_id,
                    relationship="contains",
                    metadata={"source": "doc_section_hierarchy"},
                    created_at=timestamp,
                )
            )

        return nodes, edges

    def extract_entities(self, text: str) -> List[Dict]:
        """
        Extract entities from text using reasoning model.
        """
        try:
            system_prompt = ENTITY_PROMPT
            
            user_prompt = f"Extract entities from:\n\n{text[:1000]}"
            
            response = create_chat_completion(
                self.openai_client,
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1
            )
            
            result = response.choices[0].message.content.strip()
            
            # Try to extract JSON from response
            try:
                entities_data = json.loads(result)
                return entities_data.get("entities", [])
            except json.JSONDecodeError:
                # Fallback: parse entities manually if JSON fails
                entities = []
                lines = result.split('\n')
                for line in lines:
                    if '-' in line or '•' in line:
                        # Simple entity extraction
                        parts = line.split(':', 1)
                        if len(parts) > 1:
                            entities.append({
                                "name": parts[0].strip(' -•'),
                                "type": "entity",
                                "description": parts[1].strip()
                            })
                return entities
            
        except Exception as e:
            print(f"[!] Entity extraction failed: {e}")
            return []
    
    def build_relationships(self, entities: List[Dict], context: str) -> List[Dict]:
        """
        Build relationships between entities using reasoning model.
        """
        try:
            system_prompt = RELATION_PROMPT
            
            entities_text = "\n".join([f"- {e['name']} ({e['type']})" for e in entities])
            user_prompt = f"""Entities:\n{entities_text}\n\nContext:\n{context[:1000]}\n\nFind relationships between these entities."""
            
            response = create_chat_completion(
                self.openai_client,
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1
            )
            
            result = response.choices[0].message.content.strip()
            
            # Try to extract JSON from response
            try:
                relationships_data = json.loads(result)
                return relationships_data.get("relationships", [])
            except json.JSONDecodeError:
                # Fallback: parse relationships manually if JSON fails
                relationships = []
                lines = result.split('\n')
                for line in lines:
                    if '→' in line or '->' in line or 'relates to' in line.lower():
                        # Simple relationship extraction
                        parts = line.split('→' if '→' in line else '->' if '->' in line else 'relates to')
                        if len(parts) > 1:
                            relationships.append({
                                "source": parts[0].strip(' -•'),
                                "target": parts[1].strip(' -•'),
                                "relationship": "related_to",
                                "confidence": 0.7
                            })
                return relationships
            
        except Exception as e:
            print(f"[!] Relationship building failed: {e}")
            return []
    
    def create_knowledge_nodes(self, entities: List[Dict]) -> List[KnowledgeNode]:
        """
        Legacy node builder preserved for compatibility (unused in new ingestion path).
        """
        nodes: List[KnowledgeNode] = []
        timestamp = datetime.now().isoformat()

        texts: list[str] = []
        labels: list[str] = []
        meta: list[dict] = []
        for entity in entities:
            label = self._sanitize_label(entity.get("name", "Entity"))
            labels.append(label)
            texts.append(f"{label} {entity.get('description', '')}".strip())
            meta.append(entity)

        embeddings: list[Optional[list[float]]] = [None] * len(texts)
        if texts:
            try:
                response = self.openai_client.embeddings.create(
                    model=self.embedding_model,
                    input=texts,
                )
                embeddings = [row.embedding for row in response.data]
            except Exception as exc:  # noqa: BLE001
                print(f"[!] Batched embedding generation failed: {exc}")

        seen: set[str] = set()
        for label, emb, entity in zip(labels, embeddings, meta):
            norm = slugify(label.lower())
            if norm in seen:
                continue
            seen.add(norm)
            node = KnowledgeNode(
                id=f"ENT:{norm}",
                type=entity.get("type", "entity"),
                label=label,
                description=entity.get("description", ""),
                embedding=emb,
                metadata={"source": "cag_extraction"},
                created_at=timestamp,
            )
            nodes.append(node)

        return nodes

    def create_knowledge_edges(self, relationships: List[Dict], nodes: List[KnowledgeNode]) -> List[KnowledgeEdge]:
        """
        Legacy edge builder preserved for compatibility (unused in new ingestion path).
        """
        edges = []
        timestamp = datetime.now().isoformat()
        node_lookup = {node.label: node for node in nodes}

        for rel in relationships:
            source_node = node_lookup.get(rel['source'])
            target_node = node_lookup.get(rel['target'])

            if source_node and target_node:
                relationship = self._sanitize_relationship(rel.get("relationship", "related_to"))
                edge = KnowledgeEdge(
                    id=str(uuid.uuid4()),
                    source_id=source_node.id,
                    target_id=target_node.id,
                    relationship=relationship,
                    weight=rel.get('confidence', 1.0),
                    metadata={'source': 'cag_extraction'},
                    created_at=timestamp
                )
                edges.append(edge)

        return edges
    
    def semantic_cluster_chunks(self, chunks: List[str], n_clusters: int = 5) -> List[SemanticCluster]:
        """
        Cluster chunks semantically using embeddings.
        """
        if len(chunks) < n_clusters:
            n_clusters = len(chunks)
        
        try:
            # Generate embeddings for all chunks
            embeddings = []
            for chunk in chunks:
                embedding = self.openai_client.embeddings.create(
                    model=self.embedding_model,
                    input=chunk
                ).data[0].embedding
                embeddings.append(embedding)
            
            # Simple clustering using centroid-based approach
            # For production, use proper clustering algorithms
            clusters = []
            chunk_per_cluster = len(chunks) // n_clusters
            
            for i in range(n_clusters):
                start_idx = i * chunk_per_cluster
                end_idx = start_idx + chunk_per_cluster if i < n_clusters - 1 else len(chunks)
                
                cluster_chunks = chunks[start_idx:end_idx]
                cluster_embeddings = embeddings[start_idx:end_idx]
                
                # Calculate centroid
                centroid = np.mean(cluster_embeddings, axis=0).tolist()
                
                # Generate topic for cluster
                cluster_text = " ".join(cluster_chunks[:3])  # Use first 3 chunks for topic
                topic = self._generate_cluster_topic(cluster_text)
                
                cluster = SemanticCluster(
                    id=str(uuid.uuid4()),
                    centroid=centroid,
                    chunks=cluster_chunks,
                    topic=topic,
                    confidence=0.8  # Placeholder confidence
                )
                clusters.append(cluster)
            
            return clusters
            
        except Exception as e:
            print(f"[!] Semantic clustering failed: {e}")
            return []
    
    def _generate_cluster_topic(self, text: str) -> str:
        """
        Generate a topic name for a cluster using reasoning model.
        """
        try:
            response = create_chat_completion(
                self.openai_client,
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": CLUSTER_PROMPT},
                    {"role": "user", "content": text[:500]}
                ],
                temperature=0.3
            )
            return response.choices[0].message.content.strip()
        except:
            return "Unknown Topic"
    
    def traverse_knowledge_graph(self, query: str, max_depth: int = 2) -> List[str]:
        """
        Traverse knowledge graph to find related concepts.
        """
        try:
            # Find relevant nodes for query
            query_embedding = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=query
            ).data[0].embedding
            # Graph expansion is not yet implemented; return empty to avoid
            # unbounded table scans.
            _ = query_embedding  # keep linting happy
            return []
        except Exception as e:
            print(f"[!] Graph traversal failed: {e}")
            return []

    def _get_hybrid_retriever(self) -> HybridRetrievalService:
        if self._hybrid_retriever is None:
            if self._supabase_url and self._supabase_key:
                config = RetrievalConfig.from_env(
                    supabase_url=self._supabase_url,
                    supabase_key=self._supabase_key,
                )
            else:
                config = RetrievalConfig.from_env()
            self._hybrid_retriever = HybridRetrievalService(config)
        return self._hybrid_retriever

    def _render_hybrid_result(self, result: HybridRetrievalResult) -> str:
        blocks: List[str] = []

        for doc in result.documents:
            doc_id = str(doc.doc_id or "unknown")
            header = f"[Document id={doc_id}]"
            if doc.similarity is not None:
                header = f"[Document id={doc_id} score={doc.similarity:.3f}]"
            source = doc.metadata.get("section_id") or doc.metadata.get("tags") or "unknown"
            prefix = f"{header} {source or ''}".strip()
            blocks.append(f"{prefix}\n{doc.content}")

        if result.graph_nodes:
            lines = []
            for node in result.graph_nodes[:10]:
                title = node.get("title") or node.get("id", "Node")
                summary = node.get("attrs", {}).get("description", "")
                lines.append(f"- {title}: {summary}")
            blocks.append("Graph facts:\n" + "\n".join(lines))

        if result.telemetry:
            notes = ", ".join(result.telemetry.notes) if result.telemetry.notes else "ok"
            blocks.append(
                f"[retrieval] docs_examined={result.telemetry.documents_examined} "
                f"latency={result.telemetry.latency_ms:.1f}ms notes={notes}"
            )

        return "\n\n---\n\n".join(blocks)

    def _query_variants(self, query: str) -> list[str]:
        """
        Build retrieval-friendly query variants for noisy OCR/MCQ prompts.
        """
        cleaned = " ".join((query or "").replace("\t", " ").split())
        if not cleaned:
            return []

        variants: list[str] = [cleaned]

        # MCQ/OCR variant: keep only the stem up to the first question mark.
        qm_idx = cleaned.find("?")
        if qm_idx != -1:
            stem_q = cleaned[: qm_idx + 1].strip()
            if stem_q and stem_q not in variants:
                variants.append(stem_q)

        # Strip common hyphen-led Yes/No option trails often produced by OCR.
        no_hyphen_options = re.sub(
            r"\s+-\s+(?:yes|no)\b.*?(?=(\s+-\s+(?:yes|no)\b)|$)",
            " ",
            cleaned,
            flags=re.I,
        )
        no_hyphen_options = " ".join(no_hyphen_options.split())
        if no_hyphen_options and no_hyphen_options not in variants:
            variants.append(no_hyphen_options)

        # Remove common option labels and keep only the core stem.
        no_options = re.sub(
            r"\(?[A-Da-d]\s*[\)\.\:]\s+[^()]+?(?=\s+\(?[A-Da-d]\s*[\)\.\:]|\s*$)",
            " ",
            cleaned,
        )
        no_options = " ".join(no_options.split())
        if no_options and no_options != cleaned:
            variants.append(no_options)

        # Keep text up to the first explicit option marker as a final stem variant.
        stem = re.split(r"\s+\(?[A-Da-d]\s*[\)\.\:]\s+", cleaned, maxsplit=1)[0].strip()
        if stem and stem not in variants:
            variants.append(stem)

        return variants

    def _vector_search(
        self,
        query_text: str,
        top_k: int,
        threshold: float,
        group_prefix: str | None = None,
        profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query_embedding = self.openai_client.embeddings.create(
            model=self.embedding_model,
            input=query_text,
        ).data[0].embedding

        base_payload: dict[str, Any] = {
            "query_embedding": query_embedding,
            "match_threshold": threshold,
            "match_count": top_k,
        }
        extended_payload: dict[str, Any] = {
            **base_payload,
            "group_prefix": group_prefix,
            "profile_filter": profile_id,
        }

        try:
            vector_results = self.supabase.rpc("match_documents", extended_payload).execute()
        except Exception as exc:
            # Backward compatibility for deployments that only have the legacy
            # 3-arg RPC signature. If a caller requested explicit filters,
            # keep failing rather than silently dropping filter constraints.
            if (
                group_prefix is None
                and profile_id is None
                and self._is_match_documents_signature_error(exc)
            ):
                vector_results = self.supabase.rpc("match_documents", base_payload).execute()
            else:
                raise

        results: list[dict[str, Any]] = []
        for row in (vector_results.data or []):
            content = (row.get("content") or "").strip()
            if not content:
                continue
            score = row.get("similarity")
            try:
                score = float(score) if score is not None else None
            except Exception:
                score = None
            results.append(
                {
                    "id": str(row.get("id") or row.get("doc_id") or "unknown"),
                    "content": content,
                    "score": score,
                    "section_id": row.get("meta", {}).get("section_id") if isinstance(row.get("meta"), dict) else None,
                }
            )
        return results

    @staticmethod
    def _is_match_documents_signature_error(exc: Exception) -> bool:
        message = str(exc)
        if "match_documents" not in message:
            return False
        return "PGRST203" in message or "PGRST202" in message

    def enhanced_retrieve_context(
        self,
        query: str,
        top_k: int = 5,
        *,
        profile_id: str | None = None,
    ) -> str:
        """
        Enhanced context retrieval combining vector search and knowledge graph traversal.
        """
        if self.use_hybrid_retrieval:
            try:
                filters = {"profile_id": normalize_profile_id(profile_id)} if profile_id else None
                result = self._get_hybrid_retriever().retrieve_context(
                    query,
                    match_count=top_k,
                    filters=filters,
                )
                rendered = self._render_hybrid_result(result)
                if rendered:
                    return rendered[:4000]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Hybrid retrieval failed, falling back to legacy path: %s", exc)

        # Legacy path: vector similarity + ad-hoc KG traversal with retries for OCR/MCQ noise.
        try:
            vector_matches: list[dict[str, Any]] = []
            top_k = max(top_k, 12)
            query_variants = self._query_variants(query)
            thresholds = (0.2, 0.12, 0.08, 0.03, 0.0)

            for q in query_variants:
                for threshold in thresholds:
                    vector_matches = self._vector_search(
                        q,
                        top_k=top_k,
                        threshold=threshold,
                        profile_id=normalize_profile_id(profile_id) if profile_id else None,
                    )
                    if vector_matches:
                        logger.info(
                            "Retrieved %s vector chunks (threshold=%.2f, query_len=%s)",
                            len(vector_matches),
                            threshold,
                            len(q),
                        )
                        break
                if vector_matches:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Legacy vector search failed: %s", exc)
            vector_matches = []

        vector_context: list[str] = []
        for match in vector_matches:
            header = f"[Document id={match['id']}"
            if match.get("score") is not None:
                header += f" score={match['score']:.3f}"
            if match.get("section_id"):
                header += f" section={match['section_id']}"
            header += "]"
            vector_context.append(f"{header}\n{match['content']}")

        graph_context = self.traverse_knowledge_graph(query)
        all_context = vector_context + graph_context
        
        if not all_context:
            return ""
        
        combined = "\n\n---\n\n".join(all_context)
        return combined[:4000]  # Limit to 4000 chars
    
    def _build_episode(
        self,
        text: str,
        source: str,
        *,
        profile_id: str | None = None,
    ) -> EpisodePayload:
        """Normalize text into an EpisodePayload for ingestion."""
        paragraphs = split_into_paragraphs(text)
        chunks = chunk_text(paragraphs, chunk_size=1200, overlap=150)
        source_slug = safe_doc_slug(Path(source).stem if source else "cag")
        profile = normalize_profile_id(profile_id) if profile_id else source_slug
        group_id = compose_group_id(profile, "cag", source_slug)
        episode_id = f"EP:{group_id}:{uuid.uuid4().hex[:8]}"
        episode_chunks = [
            EpisodeChunk(
                chunk_id=f"{group_id}:{idx:03d}",
                text=chunk,
                metadata={"source": source, "order": idx},
            )
            for idx, chunk in enumerate(chunks, start=1)
        ]
        return EpisodePayload(
            episode_id=episode_id,
            source=source or "cag_agent",
            source_type="text",
            reference_time=datetime.utcnow(),
            group_id=group_id,
            profile_id=profile,
            tags=["cag"],
            chunks=episode_chunks,
            raw_text=text,
        )

    def process_document_with_cag(
        self,
        text: str,
        source: str = "cag_agent",
        *,
        profile_id: str | None = None,
    ) -> Dict:
        """
        Process document with full CAG pipeline using the unified ingestion path.
        """
        print("[*] Processing with CAG pipeline (Episode → Extraction → Supabase)...")
        payload = self._build_episode(text, source, profile_id=profile_id)
        ingestion = self._get_ingestion_service().ingest_episode(payload)
        return {
            "nodes_stored": ingestion.nodes_written,
            "edges_stored": ingestion.edges_written,
            "documents_stored": ingestion.documents_written,
            "profile_id": payload.profile_id,
            "group_id": payload.group_id,
            "warnings": ingestion.warnings,
        }
    
    def resolve_reasoning_runtime(
        self,
        *,
        platform: Optional[str] = None,
        model: Optional[str] = None,
        ollama_target: Optional[str] = None,
    ) -> Dict[str, Optional[str]]:
        """Resolve provider/model routing for a single reasoning request."""
        platform_raw = (platform or "").strip().lower()
        model_raw = (model or "").strip()

        if not platform_raw:
            if model_raw and ":" in model_raw:
                platform_raw = "ollama"
            else:
                platform_raw = (os.getenv("REASON_PLATFORM", "openai") or "openai").strip().lower()

        if platform_raw not in {"openai", "ollama"}:
            raise ValueError("Invalid platform. Expected 'openai' or 'ollama'.")

        if platform_raw == "ollama":
            if model_raw:
                resolved_model = model_raw
            else:
                # Prefer an explicit Ollama default before falling back to REASON_MODEL.
                ollama_default_model = (
                    os.getenv("OLLAMA_REASON_MODEL")
                    or os.getenv("OLLAMA_MODEL")
                    or ""
                ).strip()
                if not ollama_default_model and REASON_MODEL and ":" in REASON_MODEL:
                    ollama_default_model = REASON_MODEL
                if not ollama_default_model:
                    raise ValueError(
                        "No Ollama model provided. Set the request model or OLLAMA_REASON_MODEL."
                    )
                resolved_model = ollama_default_model
        else:
            resolved_model = model_raw or REASON_MODEL
            if not resolved_model:
                raise ValueError("No reasoning model configured.")

        runtime: Dict[str, Optional[str]] = {
            "platform": platform_raw,
            "model": resolved_model,
            "ollama_target": None,
            "ollama_host": None,
            "ollama_api_key": None,
        }

        if platform_raw == "ollama":
            target = (ollama_target or os.getenv("OLLAMA_TARGET", "cloud")).strip().lower()
            if target not in {"local", "cloud"}:
                raise ValueError("Invalid ollama_target. Expected 'local' or 'cloud'.")

            if target == "local":
                host = (os.getenv("OLLAMA_LOCAL_HOST") or "http://127.0.0.1:11434").strip()
                api_key = (os.getenv("OLLAMA_LOCAL_API_KEY") or "").strip() or None
            else:
                host = (os.getenv("OLLAMA_CLOUD_HOST") or OLLAMA_HOST or "").strip()
                api_key = (os.getenv("OLLAMA_CLOUD_API_KEY") or OLLAMA_API_KEY or "").strip() or None

            if not host:
                raise ValueError("Ollama host is not configured for selected target.")

            runtime.update(
                {
                    "ollama_target": target,
                    "ollama_host": host,
                    "ollama_api_key": api_key,
                }
            )

        return runtime

    def answer_with_enhanced_cag(
        self,
        question: str,
        *,
        platform: Optional[str] = None,
        model: Optional[str] = None,
        ollama_target: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Answer question using enhanced CAG with knowledge graph traversal.
        """
        print("[*] Enhanced CAG processing...")
        
        # Step 1: Enhanced context retrieval
        context = self.enhanced_retrieve_context(question, profile_id=profile_id)
        print(f"[+] Retrieved {len(context)} chars of enhanced context")
        
        runtime = self.resolve_reasoning_runtime(
            platform=platform,
            model=model,
            ollama_target=ollama_target,
        )

        # Step 2: Generate answer
        answer = self._generate_answer_with_context(question, context, runtime=runtime)
        
        return context, answer

    def _generate_answer_with_context(
        self,
        question: str,
        context: str,
        *,
        runtime: Optional[Dict[str, Optional[str]]] = None,
    ) -> str:
        """
        Generate answer using enhanced context.
        """
        try:
            runtime = runtime or self.resolve_reasoning_runtime()
            strict_mode = self._strict_grounded_mode()
            allowed_citation_ids = set(self._extract_context_citation_ids(context))
            if strict_mode and not context.strip():
                return self._abstain_response(
                    "No supporting context was retrieved for this question."
                )

            system_prompt = ANSWER_PROMPT
            if strict_mode:
                allowed = ", ".join(sorted(allowed_citation_ids)) or "NONE"
                system_prompt += (
                    "\n\nStrict citation scope for this request: "
                    f"{allowed}. Any citation outside this set is invalid."
                )

            user_prompt = f"""Enhanced Context (includes related concepts):
{context[:4000]}

Question: {question}

Instructions:
- Use the enhanced context to find the answer
- Consider the related concepts and relationships
- For multiple choice, select the most logical option

Answer:"""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            platform = runtime.get("platform") or "openai"
            model = runtime.get("model") or REASON_MODEL
            print(
                f"[*] Reasoning runtime: platform={platform} model={model} "
                f"ollama_target={runtime.get('ollama_target') or '-'}"
            )

            answer = self._chat_completion_text(
                messages=messages,
                runtime=runtime,
                temperature=0.2,
            )

            if strict_mode:
                repaired_once = False
                if not self._has_required_sections(answer):
                    try:
                        answer = self._repair_grounded_answer_format(
                            question=question,
                            context=context,
                            draft_answer=answer,
                            allowed_ids=allowed_citation_ids,
                            runtime=runtime,
                        )
                        repaired_once = True
                    except Exception:
                        pass
                if not self._has_required_sections(answer):
                    return self._abstain_response(
                        "Model output did not meet required grounded format."
                    )
                if not self._citations_valid(answer, allowed_citation_ids):
                    if not repaired_once:
                        try:
                            answer = self._repair_grounded_answer_format(
                                question=question,
                                context=context,
                                draft_answer=answer,
                                allowed_ids=allowed_citation_ids,
                                runtime=runtime,
                            )
                        except Exception:
                            pass
                    if not self._citations_valid(answer, allowed_citation_ids):
                        answer = self._fallback_repair_citations(
                            answer,
                            allowed_citation_ids,
                        )
                    if not self._citations_valid(answer, allowed_citation_ids):
                        return self._abstain_response(
                            "Model output had missing/invalid citations for retrieved evidence."
                        )
                try:
                    supported, reason = self._grounding_supported(
                        question=question,
                        context=context,
                        answer=answer,
                        runtime=runtime,
                    )
                except Exception as exc:
                    return self._abstain_response(
                        f"Grounding verification failed: {exc}"
                    )
                if not supported:
                    return self._abstain_response(reason)
            return answer
        except Exception as e:
            print(f"[!] Enhanced answer generation failed: {e}")
            return f"Error: Unable to generate answer. ({e})"


def extract_text_from_file(file_path: str) -> str:
    """
    Extract text from various file formats (PDF, TXT, etc.).
    """
    if file_path.lower().endswith('.pdf'):
        try:
            doc = fitz.open(file_path)
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            return text
        except Exception as e:
            raise ValueError(f"Failed to extract text from PDF: {e}")
    else:
        # Assume text file
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # Try with different encoding
            with open(file_path, 'r', encoding='latin-1') as f:
                return f.read()


def _extract_cli_answer_sections(answer_text: str) -> Dict[str, str]:
    """Normalize answer text into Question/Answer/Rationale/Citations sections."""
    sections = {"Answer": "", "Rationale": "", "Citations": ""}
    current: Optional[str] = None

    for raw_line in (answer_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("answer:"):
            current = "Answer"
            sections[current] = line.split(":", 1)[1].strip()
            continue
        if lowered.startswith("rationale:"):
            current = "Rationale"
            sections[current] = line.split(":", 1)[1].strip()
            continue
        if lowered.startswith("citations:"):
            current = "Citations"
            sections[current] = line.split(":", 1)[1].strip()
            continue
        if current:
            sections[current] = f"{sections[current]} {line}".strip()

    # Fallback for non-structured model output.
    if not any(sections.values()):
        sections["Answer"] = (answer_text or "").strip()

    if not sections["Rationale"]:
        sections["Rationale"] = "N/A"
    if not sections["Citations"]:
        sections["Citations"] = "NONE"

    return sections


def main():
    """Main entry point for CAG Agent."""
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        print(f"[!] Environment validation failed: {exc}")
        return 1

    parser = argparse.ArgumentParser(description="Context-Aware Grouping (CAG) Agent")
    parser.add_argument(
        "text_file",
        nargs="?",
        help="Text file to process with CAG"
    )
    parser.add_argument(
        "--question",
        "-q",
        help="Question to answer using enhanced CAG"
    )
    parser.add_argument(
        "--process",
        "-p",
        action="store_true",
        help="Process document with full CAG pipeline"
    )
    parser.add_argument(
        "--answer",
        "-a",
        action="store_true",
        help="Answer question using enhanced CAG"
    )
    parser.add_argument(
        "--profile",
        help="Knowledge profile namespace to use for ingestion/retrieval.",
    )
    
    args = parser.parse_args()
    
    cag = CAGAgent()
    
    if args.process and args.text_file:
        # Process document with CAG
        print("[*] Processing with CAG pipeline...")
        
        try:
            # Extract text from file (supports PDF and text files)
            text = extract_text_from_file(args.text_file)
            print(f"[*] Extracted {len(text)} characters from {os.path.basename(args.text_file)}")
            
            result = cag.process_document_with_cag(
                text,
                source=args.text_file,
                profile_id=args.profile,
            )
            print(f"\n[*] CAG Processing Complete:")
            print(f"   Nodes Stored: {result['nodes_stored']}")
            print(f"   Edges Stored: {result['edges_stored']}")
            print(f"   Documents Stored: {result['documents_stored']}")
            print(f"   Profile: {result.get('profile_id')}")
            print(f"   Group: {result.get('group_id')}")
            
        except FileNotFoundError:
            print(f"[!] File not found: {args.text_file}")
        except Exception as e:
            print(f"[!] Processing failed: {e}")
    
    elif args.answer:
        # Answer question with enhanced CAG
        if args.question:
            question = args.question
        else:
            question = input("Enter your question: ")
        # Suppress internal diagnostic prints for clean CLI output.
        with contextlib.redirect_stdout(io.StringIO()):
            _, answer = cag.answer_with_enhanced_cag(question, profile_id=args.profile)
        sections = _extract_cli_answer_sections(answer)
        print(f"Question: {question}\n")
        print(f"Answer: {sections['Answer']}\n")
        print(f"Rationale: {sections['Rationale']}\n")
        print(f"Citations: {sections['Citations']}")
    
    else:
        print("[*] CAG Agent - Context-Aware Grouping with Knowledge Graphs")
        print("Use --process <file> to process documents")
        print("Use --answer <question> to answer questions")
        print("Example: python -m study_agents.cag_agent --process doc.txt --process")


if __name__ == "__main__":
    raise SystemExit(main())
