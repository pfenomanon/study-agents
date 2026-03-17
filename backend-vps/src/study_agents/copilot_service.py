"""
FastAPI service that wraps a PydanticAI agent and existing study-agents tools for CopilotKit frontends.

Endpoints:
- POST /copilot/chat { "message": "..." }  -> agent-run answer + traces

The agent exposes typed tools for:
- ask_question: local CAGAgent.enhanced_retrieve_context + answer
- rag_build: invoke RAG builder (no Supabase push by default)
- web_research: trigger the web research agent

This service keeps secret keys server-side; a Next.js/CopilotKit frontend can call it via a simple proxy.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi import Header, Depends
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, Tool

from .cag_agent import CAGAgent
from .rag_reasoning import RAGBuildAgent
from .rag_builder_core import ensure_dir
from .web_research_agent import WebResearchAgent
from .cag_agent import extract_text_from_file
from .vision_agent import run_capture_once
from .security import extract_auth_token, token_matches
from .settings import SettingsError, get_settings


app = FastAPI(title="Study Agents Copilot Service")
COPILOT_API_KEY = (os.getenv("COPILOT_API_KEY") or os.getenv("API_TOKEN") or "").strip()
COPILOT_REQUIRE_TOKEN = os.getenv("COPILOT_REQUIRE_TOKEN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if COPILOT_REQUIRE_TOKEN and not COPILOT_API_KEY:
    raise RuntimeError(
        "Copilot API token is required but missing. "
        "Set COPILOT_API_KEY/API_TOKEN or disable with COPILOT_REQUIRE_TOKEN=false."
    )


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    traces: dict[str, Any]


class CaptureRegion(BaseModel):
    top: int
    left: int
    width: int
    height: int


class CaptureRequest(BaseModel):
    monitor: int = 1
    mode: str = "local"
    top_offset: Optional[int] = None
    bottom_offset: Optional[int] = None
    left_offset: Optional[int] = None
    right_offset: Optional[int] = None
    region: Optional[CaptureRegion] = None
    remote_cag_url: Optional[str] = None
    remote_image_url: Optional[str] = None
    remote_mcp_url: Optional[str] = None


async def require_api_key(x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    if not COPILOT_REQUIRE_TOKEN:
        return
    provided = extract_auth_token({"X-API-Key": x_api_key or "", "Authorization": authorization or ""})
    if not provided:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not token_matches(COPILOT_API_KEY, provided):
        raise HTTPException(status_code=403, detail="Forbidden")


def _build_tools():
    cag = CAGAgent()
    rag_agent = RAGBuildAgent()
    cag = CAGAgent()

    @Tool
    def ask_question(ctx: RunContext[dict], question: str) -> dict:
        """
        Answer a question using the CAG pipeline with enhanced retrieval.
        """
        context = cag.enhanced_retrieve_context(question)
        answer = cag._generate_answer_with_context(question, context)
        return {"answer": answer, "context": context}

    @Tool
    def rag_build(ctx: RunContext[dict], pdf_path: str, outdir: str = "data/output") -> dict:
        """
        Build a RAG bundle for a PDF. Returns artifact paths; does not push to Supabase.
        """
        outdir_path = ensure_dir(Path(outdir))
        result = rag_agent.build_bundle(pdf_path=Path(pdf_path), outdir=outdir_path)
        return {
            "artifacts": result.artifacts,
            "plan": asdict(result.plan),
        }

    @Tool
    async def web_research(ctx: RunContext[dict], url: str, max_depth: int = 2, max_pages: int = 10, query: Optional[str] = None) -> dict:
        """
        Run the web research agent against a URL. Returns the output markdown path and stats.
        """
        agent = WebResearchAgent(
            output_dir=Path("research_output"),
            query=query or "",
            use_llm_relevance=False,
            download_docs=False,
            auto_ingest=False,
            ingest_threshold=0.6,
            ingest_group="copilot",
            ingest_chunk_size=1200,
            ingest_overlap=150,
            resume_file=None,
            resume_reset=False,
        )
        results = await agent.research_from_url(url, max_depth=max_depth, max_pages=max_pages)
        rag_path = agent.prepare_rag_content(results)
        return {
            "pages": len(results),
            "average_relevance": (
                sum(r.relevance_score for r in results) / len(results) if results else 0.0
            ),
            "rag_markdown": str(rag_path),
        }

    @Tool
    def cag_process(ctx: RunContext[dict], file_path: str) -> dict:
        """
        Run the CAG pipeline on a file (PDF or markdown) and ingest into Supabase.
        """
        text = extract_text_from_file(file_path)
        result = cag.process_document_with_cag(text, source=file_path)
        return result

    @Tool
    def vision_capture(
        ctx: RunContext[dict],
        monitor: int = 1,
        mode: str = "local",
        top_offset: Optional[int] = None,
        bottom_offset: Optional[int] = None,
        left_offset: Optional[int] = None,
        right_offset: Optional[int] = None,
        region: Optional[dict] = None,
        remote_cag_url: Optional[str] = None,
        remote_image_url: Optional[str] = None,
    ) -> dict:
        """
        Capture the screen and run OCR + answering. Region can be a dict with top/left/width/height.
        """
        try:
            return run_capture_once(
                mode=mode,
                remote_cag_url=remote_cag_url,
                remote_image_url=remote_image_url,
                monitor_index=monitor,
                top_offset=top_offset,
                bottom_offset=bottom_offset,
                left_offset=left_offset,
                right_offset=right_offset,
                region=region,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    return [ask_question, rag_build, web_research, cag_process, vision_capture]


def _build_agent() -> Agent[dict]:
    system = (
        "You are the Study Agents orchestrator. Use the provided tools to answer user requests. "
        "Prefer ask_question for Q&A, rag_build for PDFs, web_research for URL crawls. "
        "Always include concise reasoning and keep responses brief."
    )
    return Agent(
        model="openai:gpt-4o-mini",
        tools=_build_tools(),
        output_type=str,
        system_prompt=system,
    )


agent = _build_agent()


@app.post("/copilot/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def copilot_chat(req: ChatRequest) -> ChatResponse:
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    try:
        result = await agent.run(req.message)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    final_output = getattr(result, "output", None) or getattr(result, "response", None) or result
    traces = {"final": final_output}
    messages = getattr(result, "all_messages_json", None)
    if callable(messages):
        messages = messages()
    if messages is not None:
        traces["messages"] = messages

    reply = final_output if isinstance(final_output, str) else json.dumps(final_output)
    return ChatResponse(reply=reply, traces=traces)


@app.post("/copilot/capture", dependencies=[Depends(require_api_key)])
async def copilot_capture(req: CaptureRequest):
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    try:
        result = run_capture_once(
            mode=req.mode,
            remote_cag_url=req.remote_cag_url,
            remote_mcp_url=req.remote_mcp_url,
            remote_image_url=req.remote_image_url,
            monitor_index=req.monitor,
            top_offset=req.top_offset,
            bottom_offset=req.bottom_offset,
            left_offset=req.left_offset,
            right_offset=req.right_offset,
            region=req.region.dict() if req.region else None,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result

@app.post("/copilot/cag-process", dependencies=[Depends(require_api_key)])
async def copilot_cag_process(req: ChatRequest):
    """
    Direct CAG processing endpoint to avoid relying on LLM tool selection.
    """
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    try:
        file_path = req.message.strip()
        if not file_path:
            raise HTTPException(status_code=400, detail="Missing file path")
        text = extract_text_from_file(file_path)
        result = CAGAgent().process_document_with_cag(text, source=file_path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"ok": True, "result": result}


def main():
    import uvicorn

    uvicorn.run(
        "study_agents.copilot_service:app",
        host="0.0.0.0",
        port=9010,
        reload=False,
    )


if __name__ == "__main__":
    main()
