#!/usr/bin/env python3
"""Test ollama cloud connection."""

import os
import sys
sys.path.insert(0, 'src')

from study_agents.config import OLLAMA_API_KEY, OLLAMA_HOST

def main():
    print(f"OLLAMA_HOST from config: {OLLAMA_HOST}")
    print(f"OLLAMA_API_KEY from config: {OLLAMA_API_KEY[:10]}...")
    
    # Set environment variables
    os.environ["OLLAMA_HOST"] = OLLAMA_HOST
    os.environ["OLLAMA_API_KEY"] = OLLAMA_API_KEY
    
    # Try direct ollama.chat
    import ollama
    try:
        result = ollama.chat(
            model="deepseek-v3.1:671b-cloud",
            messages=[{"role": "user", "content": "Hello"}]
        )
        print("✅ Direct ollama.chat works")
        print(f"Response: {result['message']['content'][:50]}...")
    except Exception as e:
        print(f"❌ Direct ollama.chat failed: {e}")
    
    # Try with explicit client
    try:
        client = ollama.Client(host=OLLAMA_HOST)
        result = client.chat(
            model="deepseek-v3.1:671b-cloud",
            messages=[{"role": "user", "content": "Hello"}]
        )
        print("✅ Client.chat works")
        print(f"Response: {result['message']['content'][:50]}...")
    except Exception as e:
        print(f"❌ Client.chat failed: {e}")

if __name__ == "__main__":
    main()
