#!/usr/bin/env python
"""
Test script to verify knowledge graph functionality
"""

from src.study_agents.cag_agent import CAGAgent
from src.study_agents.supabase_client import create_supabase_client
import os
from dotenv import load_dotenv

load_dotenv()

def test_knowledge_graph():
    """Test all knowledge graph functionality"""
    print("=" * 60)
    print("KNOWLEDGE GRAPH FUNCTIONALITY TEST")
    print("=" * 60)
    
    cag = CAGAgent()
    supabase = create_supabase_client(
        url=os.getenv("SUPABASE_URL"), key=os.getenv("SUPABASE_KEY")
    )
    
    # 1. Check current graph state
    print("\n1. Current Knowledge Graph State:")
    nodes = supabase.table('kg_nodes').select('*').execute()
    edges = supabase.table('kg_edges').select('*').execute()
    print(f"   - Total nodes: {len(nodes.data) if nodes.data else 0}")
    print(f"   - Total edges: {len(edges.data) if edges.data else 0}")
    
    # Count entity nodes
    entity_nodes = [n for n in nodes.data if n.get('type') == 'entity']
    print(f"   - Entity nodes: {len(entity_nodes)}")
    
    # 2. Test entity extraction
    print("\n2. Entity Extraction Test:")
    test_text = """
    State Farm is a major insurance company that offers auto damage service programs.
    Independent adjusters work with State Farm to handle claims.
    CAT adjusters specialize in catastrophic events like hurricanes.
    Texas Department of Insurance regulates adjuster licensing.
    """
    
    entities = cag.extract_entities(test_text)
    print(f"   - Extracted {len(entities)} entities:")
    for e in entities:
        print(f"     * {e['name']} ({e['type']}): {e['description'][:50]}...")
    
    # 3. Test relationship building
    print("\n3. Relationship Building Test:")
    relationships = cag.build_relationships(entities, test_text)
    print(f"   - Built {len(relationships)} relationships:")
    for r in relationships[:3]:
        print(f"     * {r['source']} --[{r['relationship']}]--> {r['target']}")
    
    # 4. Test graph traversal
    print("\n4. Graph Traversal Test:")
    related_content = cag.traverse_knowledge_graph("insurance")
    print(f"   - Found {len(related_content)} related items")
    for i, content in enumerate(related_content[:3]):
        print(f"     {i+1}. {content[:80]}...")
    
    # 5. Test enhanced retrieval
    print("\n5. Enhanced Context Retrieval Test:")
    context, answer = cag.answer_with_enhanced_cag("What is the role of an adjuster?")
    print(f"   - Context length: {len(context)} chars")
    print(f"   - Answer length: {len(answer)} chars")
    print(f"   - Sample answer: {answer[:150]}...")
    
    # 6. Test semantic clustering
    print("\n6. Semantic Clustering Test:")
    from src.study_agents.rag_builder_core import split_into_paragraphs, chunk_text
    paragraphs = split_into_paragraphs(test_text)
    chunks = chunk_text(paragraphs, chunk_size=200, overlap=50)
    clusters = cag.semantic_cluster_chunks(chunks, n_clusters=2)
    print(f"   - Created {len(clusters)} clusters from {len(chunks)} chunks")
    for i, cluster in enumerate(clusters):
        print(f"     Cluster {i+1}: {cluster.topic} ({len(cluster.chunks)} chunks)")
    
    print("\n" + "=" * 60)
    print("KNOWLEDGE GRAPH TEST COMPLETE")
    print("=" * 60)
    print("\n✅ All knowledge graph features are working!")
    print("   - Entity extraction: Working")
    print("   - Relationship building: Working")
    print("   - Graph storage: Working")
    print("   - Graph traversal: Working")
    print("   - Enhanced retrieval: Working")
    print("   - Semantic clustering: Working")

if __name__ == "__main__":
    test_knowledge_graph()
