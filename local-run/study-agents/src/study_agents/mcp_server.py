# src/study_agents/mcp_server_fixed.py
import sys
import json
from mcp.server.fastmcp import FastMCP

# Ensure UTF-8 encoding for stdout/stderr
if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# Import with error handling
try:
    from study_agents.vision_agent import run_capture_once
except ImportError as e:
    print(f"Import error: {e}", file=sys.stderr)
    sys.exit(1)

# Name shown to MCP clients (Claude, etc.)
mcp = FastMCP("study-agents-fixed")


@mcp.tool()
def capture_question() -> dict:
    """
    Capture the screen, OCR the question, retrieve context, and answer it.
    Returns the same dict as run_capture_once().
    """
    try:
        result = run_capture_once()
        # Ensure all strings in result are properly encoded
        if isinstance(result, dict):
            for key, value in result.items():
                if isinstance(value, str):
                    result[key] = value.encode('utf-8', errors='replace').decode('utf-8')
        return result
    except Exception as e:
        error_msg = f"Error in capture_question: {str(e)}"
        print(error_msg, file=sys.stderr)
        return {
            "ok": False,
            "question": "",
            "answer": f"Error: {error_msg}",
            "context_ids": [],
            "context_snippet": "",
            "screenshot_path": ""
        }


def main() -> None:
    # By default this runs over stdio (what Claude / MCP clients expect)
    mcp.run()


if __name__ == "__main__":
    main()
