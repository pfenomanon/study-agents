"""
Context-Aware Grouping (CAG) Agent

Enhances RAG with semantic clustering, knowledge graphs, and context-aware retrieval.
Uses Graphiti MCP for knowledge graph operations and integrates with Supabase.
"""

import argparse
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
from .prompt_loader import load_prompt
from .kg_pipeline import (
    HybridRetrievalResult,
    HybridRetrievalService,
    RetrievalConfig,
    EpisodeChunk,
    EpisodePayload,
    KnowledgeIngestionService,
)
from .settings import get_settings, SettingsError

logger = logging.getLogger(__name__)


_DEFAULT_ENTITY_PROMPT = """You are an entity extraction expert. Extract key entities from the text.
Return entities in this JSON format:
{
  "entities": [
    {"name": "Entity Name", "type": "person/concept/place", "description": "Brief description"}
  ]
}"""

_DEFAULT_RELATION_PROMPT = """You are a relationship extraction expert. Analyze the provided entities and context to find relationships.
Return relationships in this JSON format:
{
  "relationships": [
    {"source": "Entity1", "target": "Entity2", "relationship": "influences", "confidence": 0.8}
  ]
}"""

_DEFAULT_ANSWER_PROMPT = """You are the Expert Insurance Adjuster guiding another licensed adjuster.
Use the provided context (documents + graph) to deliver coverage analysis, workflow steps, and documentation reminders.
Always speak to the adjuster (use “you” to describe their actions) and never give directions to the policyholder.
For multiple choice questions, select the most appropriate answer (A, B, C, or D) and justify it briefly."""

_DEFAULT_CLUSTER_PROMPT = "Generate a short topic name (2-3 words) for the following text."

ENTITY_PROMPT = load_prompt("cag_entity_extraction.txt", _DEFAULT_ENTITY_PROMPT)
RELATION_PROMPT = load_prompt("cag_relationship_extraction.txt", _DEFAULT_RELATION_PROMPT)
ANSWER_PROMPT = load_prompt("cag_answer_generation.txt", _DEFAULT_ANSWER_PROMPT)
CLUSTER_PROMPT = load_prompt("cag_cluster_topic.txt", _DEFAULT_CLUSTER_PROMPT)


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
            
            response = self.openai_client.chat.completions.create(
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
            
            response = self.openai_client.chat.completions.create(
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
            response = self.openai_client.chat.completions.create(
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
            header = "[Document]"
            if doc.similarity is not None:
                header = f"[Document score={doc.similarity:.3f}]"
            source = doc.metadata.get("section_id") or doc.metadata.get("tags")
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
    ) -> list[str]:
        query_embedding = self.openai_client.embeddings.create(
            model=self.embedding_model,
            input=query_text,
        ).data[0].embedding

        vector_results = self.supabase.rpc(
            "match_documents",
            {
                "query_embedding": query_embedding,
                "match_threshold": threshold,
                "match_count": top_k,
            },
        ).execute()

        return [r.get("content", "") for r in (vector_results.data or []) if r.get("content")]

    def enhanced_retrieve_context(self, query: str, top_k: int = 5) -> str:
        """
        Enhanced context retrieval combining vector search and knowledge graph traversal.
        """
        if self.use_hybrid_retrieval:
            try:
                result = self._get_hybrid_retriever().retrieve_context(query, match_count=top_k)
                rendered = self._render_hybrid_result(result)
                if rendered:
                    return rendered[:4000]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Hybrid retrieval failed, falling back to legacy path: %s", exc)

        # Legacy path: vector similarity + ad-hoc KG traversal with retries for OCR/MCQ noise.
        try:
            vector_context: list[str] = []
            top_k = max(top_k, 12)
            query_variants = self._query_variants(query)
            thresholds = (0.2, 0.12, 0.08, 0.03, 0.0)

            for q in query_variants:
                for threshold in thresholds:
                    vector_context = self._vector_search(q, top_k=top_k, threshold=threshold)
                    if vector_context:
                        logger.info(
                            "Retrieved %s vector chunks (threshold=%.2f, query_len=%s)",
                            len(vector_context),
                            threshold,
                            len(q),
                        )
                        break
                if vector_context:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("Legacy vector search failed: %s", exc)
            vector_context = []
        
        graph_context = self.traverse_knowledge_graph(query)
        all_context = vector_context + graph_context
        
        if not all_context:
            return ""
        
        combined = "\n\n---\n\n".join(all_context)
        return combined[:4000]  # Limit to 4000 chars
    
    def _build_episode(self, text: str, source: str) -> EpisodePayload:
        """Normalize text into an EpisodePayload for ingestion."""
        paragraphs = split_into_paragraphs(text)
        chunks = chunk_text(paragraphs, chunk_size=1200, overlap=150)
        base = slugify(Path(source).stem if source else "cag")
        episode_id = f"EP:{base}:{uuid.uuid4().hex[:8]}"
        group_id = base
        episode_chunks = [
            EpisodeChunk(
                chunk_id=f"CHUNK:{base}:{idx:03d}",
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
            tags=["cag"],
            chunks=episode_chunks,
            raw_text=text,
        )

    def process_document_with_cag(self, text: str, source: str = "cag_agent") -> Dict:
        """
        Process document with full CAG pipeline using the unified ingestion path.
        """
        print("[*] Processing with CAG pipeline (Episode → Extraction → Supabase)...")
        payload = self._build_episode(text, source)
        ingestion = self._get_ingestion_service().ingest_episode(payload)
        return {
            "nodes_stored": ingestion.nodes_written,
            "edges_stored": ingestion.edges_written,
            "documents_stored": ingestion.documents_written,
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
    ) -> Tuple[str, str]:
        """
        Answer question using enhanced CAG with knowledge graph traversal.
        """
        print("[*] Enhanced CAG processing...")
        
        # Step 1: Enhanced context retrieval
        context = self.enhanced_retrieve_context(question)
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
            system_prompt = ANSWER_PROMPT
        
            user_prompt = f"""Enhanced Context (includes related concepts):
{context[:4000]}

Question: {question}

Instructions:
- Use the enhanced context to find the answer
- Consider the related concepts and relationships
- For multiple choice, select the most logical option

Answer:"""
            
            runtime = runtime or self.resolve_reasoning_runtime()
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

            if platform == "openai":
                response = self.openai_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                )
                return response.choices[0].message.content.strip()

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
                options={"temperature": 0.2},
            )

            if isinstance(result, dict):
                return (result.get("message", {}) or {}).get("content", "").strip()

            message = getattr(result, "message", None)
            if isinstance(message, dict):
                return (message.get("content") or "").strip()
            if message is not None and hasattr(message, "get"):
                return (message.get("content") or "").strip()
            return ""
            
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
    
    args = parser.parse_args()
    
    cag = CAGAgent()
    
    if args.process and args.text_file:
        # Process document with CAG
        print("[*] Processing with CAG pipeline...")
        
        try:
            # Extract text from file (supports PDF and text files)
            text = extract_text_from_file(args.text_file)
            print(f"[*] Extracted {len(text)} characters from {os.path.basename(args.text_file)}")
            
            result = cag.process_document_with_cag(text)
            print(f"\n[*] CAG Processing Complete:")
            print(f"   Entities: {result['entities']}")
            print(f"   Relationships: {result['relationships']}")
            print(f"   Nodes Stored: {result['nodes_stored']}")
            print(f"   Edges Stored: {result['edges_stored']}")
            print(f"   Clusters: {result['clusters']}")
            
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
        context, answer = cag.answer_with_enhanced_cag(question)
        print(f"\n[*] Enhanced Context:\n{context[:1000]}{'...' if len(context) > 1000 else ''}\n")
        print(f"[*] Answer:\n{answer}\n")
    
    else:
        print("[*] CAG Agent - Context-Aware Grouping with Knowledge Graphs")
        print("Use --process <file> to process documents")
        print("Use --answer <question> to answer questions")
        print("Example: python -m study_agents.cag_agent --process doc.txt --process")


if __name__ == "__main__":
    raise SystemExit(main())
