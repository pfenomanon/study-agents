# src/study_agents/mcp_server.py
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .vision_agent import run_capture_once

server = Server("study-agents")


# Register a simple tool that wraps run_capture_once
@server.tool("capture_question")
async def capture_question() -> dict:
    """
    Capture the current monitor, OCR the question/options,
    retrieve context from Supabase, and reason an answer.
    """
    # This is synchronous code, but it's fine to call directly here
    result = run_capture_once()
    return result


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        # NOTE: third argument is required in your mcp version
        await server.run(
            read_stream,
            write_stream,
            initialization_options={},  # you don't need anything special here
        )


if __name__ == "__main__":
    asyncio.run(main())
