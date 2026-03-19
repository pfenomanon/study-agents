#!/usr/bin/env python3
"""Test the web research agent with a simple query."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, 'src')

from study_agents.web_research_agent import WebResearchAgent
import pytest

@pytest.mark.asyncio
async def test_basic_search():
    """Test basic search functionality."""
    print("=== Testing Web Research Agent ===")
    
    agent = WebResearchAgent(output_dir=Path("test_research_output"))
    
    # Simple test query
    query = "artificial intelligence machine learning basics"
    
    print(f"\n🔍 Testing search for: {query}")
    
    # Test search only (no full research)
    urls = await agent.search_web(query, max_results=3)
    
    print(f"\n📊 Found {len(urls)} URLs:")
    for i, url in enumerate(urls, 1):
        print(f"  {i}. {url}")
    
    # Test one URL fetch
    if urls:
        test_url = urls[0]
        print(f"\n🌐 Testing fetch: {test_url}")
        
        async with agent.crawler as crawler:
            content = await crawler.fetch_page(test_url)
        
        if content:
            print(f"✅ Fetched {len(content)} characters")
            
            # Test relevance evaluation
            relevance = await agent.evaluate_relevance(test_url, content, query)
            print(f"📈 Relevance score: {relevance:.2f}")
            
            # Test markdown conversion
            markdown = await agent.convert_to_markdown(test_url, content[:5000])  # Limit for testing
            print(f"📝 Generated {len(markdown)} characters of markdown")
            print(f"📝 Preview:\n{markdown[:200]}...")
        else:
            print("❌ Failed to fetch content")


@pytest.mark.asyncio
async def test_robots_compliance():
    """Test robots.txt compliance."""
    print("\n=== Testing Robots.txt Compliance ===")
    
    agent = WebResearchAgent()
    
    test_urls = [
        "https://example.com",
        "https://github.com",
        "https://stackoverflow.com"
    ]
    
    async with agent.crawler as crawler:
        for url in test_urls:
            can_fetch = await crawler.can_fetch(url)
            print(f"  {url}: {'✅ Allowed' if can_fetch else '❌ Disallowed'}")


async def main():
    """Run all tests."""
    await test_basic_search()
    await test_robots_compliance()
    
    print("\n🎉 Tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
