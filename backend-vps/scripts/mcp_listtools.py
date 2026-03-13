import os
import sys
import anyio
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

async def main():
    print('starting client')
    server = StdioServerParameters(
        command=sys.executable,
        args=["/app/mcp_stdio_wrapper.py"],
        env={**os.environ, "PYTHONPATH": "/app:/app/src"},
        cwd="/app",
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            print('initializing...')
            init = await session.initialize()
            print('Initialized', init.protocolVersion)
            tools = await session.list_tools()
            print('Tools:', [t.name for t in tools.tools])

if __name__ == "__main__":
    anyio.run(main)
