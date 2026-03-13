import os
import sys
from datetime import timedelta
import anyio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

PYTHONPATH = "/app:/app/src"

def summarize_call(result, key):
    data = result.model_dump()
    content = data.get("content")
    structured = data.get("structuredContent")
    print(f"\n[{key}] status: error={data.get('isError')} hasContent={bool(content)} hasStructured={bool(structured)}")
    if structured and isinstance(structured, dict):
        preview = {k: structured.get(k) for k in list(structured)[:3]}
        print(f"[{key}] structured preview: {preview}")

async def main():
    server = StdioServerParameters(
        command=sys.executable,
        args=["/app/mcp_stdio_wrapper.py"],
        env={**os.environ, "PYTHONPATH": PYTHONPATH},
        cwd="/app",
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("Initialized MCP server", init.protocolVersion)
            tools = await session.list_tools()
            print("Tools exposed:", [tool.name for tool in tools.tools])

            rag_result = await session.call_tool(
                "build_rag_bundle",
                {
                    "pdf_path": "/app/data/pdf/TXInsuranceStudyGuide.pdf",
                    "outdir": "/tmp/mcp_rag_bundle",
                    "chunk_size": 512,
                    "overlap": 64,
                    "max_sections": 2,
                    "triples": 2,
                    "push": False,
                },
                read_timeout_seconds=timedelta(minutes=15),
            )
            summarize_call(rag_result, "build_rag_bundle")

            graph_result = await session.call_tool(
                "inspect_graph",
                {"question": "What are the eligibility requirements for TWIA coverage?"},
                read_timeout_seconds=timedelta(minutes=5),
            )
            summarize_call(graph_result, "inspect_graph")

if __name__ == "__main__":
    anyio.run(main)
