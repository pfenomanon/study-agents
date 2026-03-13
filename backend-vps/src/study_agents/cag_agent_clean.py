#!/usr/bin/env python3
"""
CAG Agent - Context-Aware Grouping with Knowledge Graphs
"""

import os
import json
import uuid
import numpy as np
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import datetime
import fitz  # PyMuPDF for PDF text extraction

from openai import OpenAI
from supabase import Client

# Import RAG builder utilities
from .rag_agent import chunk_text, split_into_paragraphs
from .supabase_client import create_supabase_client

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Supabase tables
SUPABASE_DOCS_TABLE = "documents"
SUPABASE_NODES_TABLE = "kg_nodes"
SUPABASE_EDGES_TABLE = "kg_edges"

# OpenAI model
OPENAI_EMBED_MODEL = "text-embedding-3-small"

@dataclass
class Entity:
    """Represents an extracted entity."""
    id: str
    type: str
    name: str
    description: str
    confidence: float
    source: str

@dataclass
class Relationship:
    """Represents a relationship between entities."""
    id: str
    source_id: str
    target_id: str
    relationship: str
    weight: float
    source: str

@dataclass
class KnowledgeNode:
    """Represents a node in the knowledge graph."""
    id: str
    type: str
    title: str
    attributes: Dict[str, Any]

@dataclass
class KnowledgeEdge:
    """Represents an edge in the knowledge graph."""
    id: str
    source_id: str
    target_id: str
    relationship: str
    weight: float
    attributes: Dict[str, Any]

@dataclass
class SemanticCluster:
    """Represents a semantic cluster of chunks."""
    id: str
    centroid: List[float]
    chunks: List[str]
    topic: str
    confidence: float

class CAGAgent:
    """
    Context-Aware Grouping Agent with Knowledge Graph capabilities.
    """
    
    def __init__(self):
        """Initialize the CAG agent."""
        if not all([OPENAI_API_KEY, SUPABASE_URL, SUPABASE_KEY]):
            raise ValueError("Missing required environment variables")
        
        self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
        self.supabase: Client = create_supabase_client(
            url=SUPABASE_URL, key=SUPABASE_KEY
        )
        self.embedding_model = OPENAI_EMBED_MODEL
    
    def extract_entities(self, text: str) -> List[Entity]:
        """
        Extract entities from text using GPT-4o.
        """
        try:
            system_prompt = """Extract entities from the text. Return a JSON object with this format:
            {
                "entities": [
                    {
                        "type": "PERSON|ORGANIZATION|CONCEPT|LOCATION|OTHER",
                        "name": "entity name",
                        "description": "brief description",
                        "confidence": 0.95
                    }
                ]
            }"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text[:2000]}
                ],
                temperature=0.1
            )
            
            result = json.loads(response.choices[0].message.content)
            entities = []
            
            for entity_data in result.get("entities", []):
                entity = Entity(
                    id=str(uuid.uuid4()),
                    type=entity_data["type"],
                    name=entity_data["name"],
                    description=entity_data["description"],
                    confidence=entity_data["confidence"],
                    source="cag_extraction"
                )
                entities.append(entity)
            
            return entities
            
        except Exception as e:
            print(f"[!] Entity extraction failed: {e}")
            return []
    
    def build_relationships(self, entities: List[Entity], text: str) -> List[Relationship]:
        """
        Build relationships between entities using GPT-4o.
        """
        try:
            # Create entity context
            entity_context = "\n".join([
                f"{i}: {e.name} ({e.type}) - {e.description}"
                for i, e in enumerate(entities)
            ])
            
            system_prompt = """Analyze relationships between entities. Return JSON:
            {
                "relationships": [
                    {
                        "source": "entity name",
                        "target": "entity name",
                        "relationship": "relationship type",
                        "weight": 0.8
                    }
                ]
            }"""
            
            user_prompt = f"""Entities:\n{entity_context}\n\nText:\n{text[:1500]}\n\nFind relationships."""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2
            )
            
            result = json.loads(response.choices[0].message.content)
            relationships = []
            
            # Create entity name to ID mapping
            name_to_id = {e.name: e.id for e in entities}
            
            for rel_data in result.get("relationships", []):
                source_name = rel_data["source"]
                target_name = rel_data["target"]
                
                if source_name in name_to_id and target_name in name_to_id:
                    relationship = Relationship(
                        id=str(uuid.uuid4()),
                        source_id=name_to_id[source_name],
                        target_id=name_to_id[target_name],
                        relationship=rel_data["relationship"],
                        weight=rel_data["weight"],
                        source="cag_extraction"
                    )
                    relationships.append(relationship)
            
            return relationships
            
        except Exception as e:
            print(f"[!] Relationship building failed: {e}")
            return []
    
    def create_knowledge_nodes(self, entities: List[Entity]) -> List[KnowledgeNode]:
        """
        Create knowledge graph nodes from entities.
        """
        nodes = []
        for entity in entities:
            node = KnowledgeNode(
                id=entity.id,
                type=entity.type,
                title=entity.name,
                attributes={
                    "description": entity.description,
                    "confidence": entity.confidence,
                    "source": entity.source,
                    "created_at": datetime.now().isoformat()
                }
            )
            nodes.append(node)
        return nodes
    
    def create_knowledge_edges(self, relationships: List[Relationship], nodes: List[KnowledgeNode]) -> List[KnowledgeEdge]:
        """
        Create knowledge graph edges from relationships.
        """
        edges = []
        for rel in relationships:
            edge = KnowledgeEdge(
                id=rel.id,
                source_id=rel.source_id,
                target_id=rel.target_id,
                relationship=rel.relationship,
                weight=rel.weight,
                attributes={
                    "source": rel.source,
                    "created_at": datetime.now().isoformat()
                }
            )
            edges.append(edge)
        return edges
    
    def store_knowledge_graph(self, nodes: List[KnowledgeNode], edges: List[KnowledgeEdge]) -> Tuple[int, int]:
        """
        Store knowledge graph in Supabase.
        """
        nodes_stored = 0
        edges_stored = 0
        
        # Store nodes
        for node in nodes:
            try:
                self.supabase.table(SUPABASE_NODES_TABLE).insert({
                    'id': node.id,
                    'type': node.type,
                    'title': node.title,
                    'attrs': node.attributes
                }).execute()
                nodes_stored += 1
            except Exception as e:
                print(f"[!] Failed to store node {node.id}: {e}")
        
        # Store edges
        for edge in edges:
            try:
                self.supabase.table(SUPABASE_EDGES_TABLE).insert({
                    'id': edge.id,
                    'src': edge.source_id,
                    'rel': edge.relationship,
                    'dst': edge.target_id,
                    'attrs': {
                        'weight': edge.weight,
                        'source': edge.attributes['source'],
                        'created_at': edge.attributes['created_at']
                    }
                }).execute()
                edges_stored += 1
            except Exception as e:
                print(f"[!] Failed to store edge {edge.id}: {e}")
        
        return nodes_stored, edges_stored
    
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
                cluster_text = " ".join(cluster_chunks[:3])
                topic = self._generate_cluster_topic(cluster_text)
                
                cluster = SemanticCluster(
                    id=str(uuid.uuid4()),
                    centroid=centroid,
                    chunks=cluster_chunks,
                    topic=topic,
                    confidence=0.8
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
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "Generate a short topic name (2-3 words) for the following text."},
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
            
            # Search for similar nodes in documents table
            results = self.supabase.rpc('match_documents', {
                'query_embedding': query_embedding,
                'match_threshold': 0.3,
                'match_count': 10
            }).execute()
            
            if not results.data:
                return []
            
            # Get related nodes through edges
            related_content = []
            visited_nodes = set()
            
            # Try to find nodes by title similarity
            node_results = self.supabase.table(SUPABASE_NODES_TABLE).select('*').execute()
            if node_results.data:
                for node in node_results.data[:5]:
                    if any(word.lower() in node['title'].lower() for word in query.split() if len(word) > 2):
                        node_id = node['id']
                        if node_id not in visited_nodes:
                            # Traverse edges
                            edges = self.supabase.table(SUPABASE_EDGES_TABLE).select('*').or_(
                                f"(src=eq.{node_id},dst=eq.{node_id})"
                            ).execute()
                            
                            for edge in edges.data:
                                connected_node_id = edge['dst'] if edge['src'] == node_id else edge['src']
                                if connected_node_id not in visited_nodes:
                                    connected_node = self.supabase.table(SUPABASE_NODES_TABLE).select('*').eq(
                                        'id', connected_node_id
                                    ).execute()
                                    
                                    if connected_node.data:
                                        node_data = connected_node.data[0]
                                        description = node_data.get('attrs', {}).get('description', '')
                                        related_content.append(f"{node_data['title']}: {description}")
                                        visited_nodes.add(connected_node_id)
            except Exception as e:
                print(f"[!] Graph traversal error: {e}")
            
            return related_content
            
        except Exception as e:
            print(f"[!] Graph traversal failed: {e}")
            return []
    
    def enhanced_retrieve_context(self, query: str, top_k: int = 5) -> str:
        """
        Enhanced context retrieval combining vector search and knowledge graph traversal.
        """
        # Vector search for similar chunks
        try:
            query_embedding = self.openai_client.embeddings.create(
                model=self.embedding_model,
                input=query
            ).data[0].embedding
            
            vector_results = self.supabase.rpc('match_documents', {
                'query_embedding': query_embedding,
                'match_threshold': 0.2,
                'match_count': top_k
            }).execute()
            
            vector_context = [r['content'] for r in vector_results.data] if vector_results.data else []
        except:
            vector_context = []
        
        # Knowledge graph traversal for related concepts
        graph_context = self.traverse_knowledge_graph(query)
        
        # Combine contexts
        all_context = vector_context + graph_context
        
        if not all_context:
            return ""
        
        # Limit total context length
        combined = "\n\n---\n\n".join(all_context)
        return combined[:4000]
    
    def process_document_with_cag(self, text: str, source: str = "cag_agent") -> Dict:
        """
        Process document with full CAG pipeline.
        """
        print("[*] Processing with CAG pipeline...")
        
        # Step 1: Extract entities
        entities = self.extract_entities(text)
        print(f"[+] Extracted {len(entities)} entities")
        
        # Step 2: Build relationships
        relationships = self.build_relationships(entities, text)
        print(f"[+] Built {len(relationships)} relationships")
        
        # Step 3: Create knowledge graph
        nodes = self.create_knowledge_nodes(entities)
        edges = self.create_knowledge_edges(relationships, nodes)
        
        # Step 4: Store knowledge graph
        nodes_stored, edges_stored = self.store_knowledge_graph(nodes, edges)
        print(f"[+] Stored {nodes_stored} nodes and {edges_stored} edges")
        
        # Step 5: Semantic clustering
        paragraphs = split_into_paragraphs(text)
        chunks = chunk_text(paragraphs, chunk_size=1200, overlap=150)
        clusters = self.semantic_cluster_chunks(chunks)
        print(f"[+] Created {len(clusters)} semantic clusters")
        
        return {
            'entities': len(entities),
            'relationships': len(relationships),
            'nodes_stored': nodes_stored,
            'edges_stored': edges_stored,
            'clusters': len(clusters)
        }
    
    def answer_with_enhanced_cag(self, question: str) -> Tuple[str, str]:
        """
        Answer question using enhanced CAG with knowledge graph traversal.
        """
        print("[*] Enhanced CAG processing...")
        
        # Step 1: Enhanced context retrieval
        context = self.enhanced_retrieve_context(question)
        print(f"[+] Retrieved {len(context)} chars of enhanced context")
        
        # Step 2: Generate answer
        answer = self._generate_answer_with_context(question, context)
        
        return context, answer

    def _generate_answer_with_context(self, question: str, context: str) -> str:
        """
        Generate answer using enhanced context.
        """
        try:
            system_prompt = """You are a precise, evidence-based assistant with access to a knowledge graph.
            Use the provided context which includes both document content and related concepts.
            For multiple choice questions, select the most appropriate answer (A, B, C, or D)."""
        
            user_prompt = f"""Enhanced Context (includes related concepts):
{context[:4000]}

Question: {question}

Instructions:
- Use the enhanced context to find the answer
- Consider the related concepts and relationships
- For multiple choice, select the most logical option

Answer:"""
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2
            )
            
            return response.choices[0].message.content.strip()
            
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
            print(f"[!] PDF extraction failed: {e}")
            return ""
    else:
        # Assume text file
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except UnicodeDecodeError:
            # Try different encoding
            with open(file_path, 'r', encoding='latin-1') as f:
                return f.read()


def main():
    """
    Main entry point for the CAG agent.
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="CAG Agent - Context-Aware Grouping with Knowledge Graphs")
    parser.add_argument("--process", metavar="FILE", help="Process document with CAG")
    parser.add_argument("--answer", action="store_true", help="Answer question using enhanced CAG")
    parser.add_argument("--question", metavar="QUESTION", help="Question to answer")
    
    args = parser.parse_args()
    
    cag = CAGAgent()
    
    if args.process:
        # Process document with CAG
        print("[*] Processing with CAG pipeline...")
        
        try:
            # Extract text from file
            text = extract_text_from_file(args.process)
            print(f"[*] Extracted {len(text)} characters from {os.path.basename(args.process)}")
            
            result = cag.process_document_with_cag(text)
            print(f"\n[*] CAG Processing Complete:")
            print(f"   Entities: {result['entities']}")
            print(f"   Relationships: {result['relationships']}")
            print(f"   Nodes Stored: {result['nodes_stored']}")
            print(f"   Edges Stored: {result['edges_stored']}")
            print(f"   Clusters: {result['clusters']}")
            
        except FileNotFoundError:
            print(f"[!] File not found: {args.process}")
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
        print("Example: python -m study_agents.cag_agent --process doc.txt")
        print("Example: python -m study_agents.cag_agent --answer --question \"What is Python?\"")


if __name__ == "__main__":
    main()
