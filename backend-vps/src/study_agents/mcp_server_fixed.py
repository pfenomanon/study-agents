# src/study_agents/mcp_server_fixed.py
from __future__ import annotations

import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

if sys.version_info >= (3, 7):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    from study_agents.vision_agent import run_capture_once
    from study_agents.rag_reasoning import RAGBuildAgent
    from study_agents.kg_pipeline import KnowledgeIngestionService, episode_from_rag_artifacts
    from study_agents.graph_inspector import main as graph_inspector_main
    from study_agents.kb_capture_agent import (
        extract_text_with_docling,
        convert_to_markdown,
        answer_with_cag,
        answer_question_with_rag,
        append_to_knowledge_base,
    )
except ImportError as exc:
    print(f"Import error: {exc}", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("study-agents-fixed")
rag_agent = RAGBuildAgent()
ingestion_service = KnowledgeIngestionService()


@mcp.tool()
def capture_question() -> dict:
    """Capture the screen via the vision agent and answer the detected question."""
    try:
        result = run_capture_once()
        if isinstance(result, dict):
            for key, value in result.items():
                if isinstance(value, str):
                    result[key] = value.encode("utf-8", errors="replace").decode("utf-8")
        return result
    except Exception as exc:  # pragma: no cover - defensive
        msg = f"Error in capture_question: {exc}"
        print(msg, file=sys.stderr)
        return {
            "ok": False,
            "question": "",
            "answer": f"Error: {msg}",
            "context_ids": [],
            "context_snippet": "",
            "screenshot_path": "",
        }


@mcp.tool()
def build_rag_bundle(
    pdf_path: str,
    outdir: str = "out",
    push: bool = False,
    profile: Optional[str] = None,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    max_sections: Optional[int] = None,
    triples: Optional[int] = None,
) -> dict:
    """Run the reasoning-driven RAG builder for a PDF and optionally push to Supabase."""
    path = Path(pdf_path).expanduser().resolve()
    overrides = {
        k: v
        for k, v in {
            "chunk_size": chunk_size,
            "overlap": overlap,
            "max_sections": max_sections,
            "triples": triples,
        }.items()
        if v is not None
    }

    build = rag_agent.build_bundle(
        pdf_path=path,
        outdir=Path(outdir).expanduser().resolve(),
        overrides=overrides,
    )
    ingest_result = None
    if push:
        payload = episode_from_rag_artifacts(path, build.artifacts, profile_id=profile)
        ingest_result = ingestion_service.ingest_episode(payload)

    return {
        "input": str(path),
        "artifacts": build.artifacts,
        "plan": asdict(build.plan),
        "ingestion": asdict(ingest_result) if ingest_result else None,
    }


@mcp.tool()
def inspect_graph(question: str = "What are the eligibility requirements for TWIA coverage?") -> dict:
    """Invoke graph_inspector to refresh Mermaid/CSV exports and answer a question."""
    process = subprocess.run(
        [sys.executable, "-m", "study_agents.graph_inspector", "--question", question],
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise RuntimeError(f"Graph inspector failed ({process.returncode}): {process.stderr.strip()}")
    return {"output": process.stdout}


@mcp.tool()
def web_research_crawl(
    start_url: str,
    max_depth: int = 2,
    max_pages: int = 20,
    outdir: str = "research_output/mcp",
    profile: str = "",
    query: str = "",
    llm_relevance: bool = False,
    download_docs: bool = False,
    max_seconds: Optional[int] = None,
) -> dict:
    """
    Run the web research agent from MCP.
    Downloads/Markdown are written under `outdir`.
    """
    args = [
        sys.executable,
        "-m",
        "study_agents.web_research_agent",
        start_url,
        str(max_depth),
        str(max_pages),
        "--outdir",
        outdir,
    ]
    if profile:
        args += ["--profile", profile]
    if query:
        args += ["--query", query]
    if llm_relevance:
        args.append("--llm-relevance")
    if download_docs:
        args.append("--download-docs")
    if max_seconds:
        args += ["--max-seconds", str(max_seconds)]

    process = subprocess.run(args, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"web_research_agent failed ({process.returncode}): {process.stderr.strip()}")
    return {"stdout": process.stdout, "outdir": str(Path(outdir).resolve())}


@mcp.tool()
def kb_extract_from_image(
    image_path: str,
    output_markdown: Optional[str] = None,
    use_cag: bool = False,
    extract_only: bool = True,
    kb_filename: str = "knowledge_base.md",
) -> dict:
    """
    Run the KB capture OCR pipeline on an existing image.
    Optionally append the Markdown (and answer) to `kb_filename`.
    """
    img = Path(image_path).expanduser().resolve()
    if not img.exists():
        raise FileNotFoundError(f"Image not found: {img}")

    extracted_text = extract_text_with_docling(img)
    if not extracted_text:
        return {"ok": False, "error": "No text detected in image."}

    markdown = convert_to_markdown(extracted_text)
    answer = None

    if not extract_only:
        if use_cag:
            ctx, answer = answer_with_cag(markdown)
        else:
            kb_path = Path(kb_filename).expanduser().resolve()
            answer = answer_question_with_rag(markdown, kb_path)

    if output_markdown:
        out_path = Path(output_markdown).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if extract_only:
            append_to_knowledge_base(f"{markdown}\n\n---\n", out_path)
        else:
            snippet = f"## Question\n{markdown}\n\n## Answer\n{answer or ''}\n\n---\n"
            append_to_knowledge_base(snippet, out_path)

    return {
        "ok": True,
        "markdown": markdown,
        "answer": answer,
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
