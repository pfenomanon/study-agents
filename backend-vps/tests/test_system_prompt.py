#!/usr/bin/env python3
"""Test the system prompt integration."""

import sys
from pathlib import Path

sys.path.insert(0, 'src')

from study_agents.web_research_agent import SystemPrompt


def test_system_prompt_loading():
    """Test that the system prompt loads correctly."""
    print("=== Testing System Prompt Loading ===")
    
    # Test with default file
    prompt = SystemPrompt()
    
    print(f"✅ System prompt loaded: {len(prompt.prompt) > 100}")
    print(f"📄 Prompt length: {len(prompt.prompt)} characters")
    
    # Test relevance evaluation prompt
    relevance_prompt = prompt.get_relevance_evaluation_prompt(
        "quantum computing", 
        "https://example.com", 
        "This is about quantum computing fundamentals..."
    )
    
    print(f"✅ Relevance prompt generated: {len(relevance_prompt) > 200}")
    print(f"📝 Contains fact-based guidance: {'fact-based' in relevance_prompt.lower()}")
    
    # Test link extraction prompt
    link_prompt = prompt.get_link_extraction_prompt(
        "Sample HTML content with links...",
        "https://example.com"
    )
    
    print(f"✅ Link extraction prompt generated: {len(link_prompt) > 200}")
    print(f"🔗 Contains authoritative guidance: {'authoritative' in link_prompt.lower()}")
    
    # Show a snippet of the main prompt
    print(f"\n📋 Main prompt preview:")
    print(prompt.prompt[:300] + "...")


def test_prompt_content():
    """Test that the prompt contains expected content."""
    print("\n=== Testing Prompt Content ===")
    
    prompt = SystemPrompt()
    
    expected_keywords = [
        "fact-based",
        "authoritative",
        "robots.txt",
        "relevance",
        "quality",
        "ethical"
    ]
    
    for keyword in expected_keywords:
        if keyword.lower() in prompt.prompt.lower():
            print(f"✅ Contains: {keyword}")
        else:
            print(f"❌ Missing: {keyword}")


if __name__ == "__main__":
    test_system_prompt_loading()
    test_prompt_content()
    print("\n🎉 System prompt tests completed!")
