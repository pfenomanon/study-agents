from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from dataclasses import asdict

from .kg_pipeline import KnowledgeIngestionService, episode_from_rag_artifacts
from .rag_reasoning import RAGBuildAgent, RAGReasoningPlanner
from .security import extract_auth_token, token_matches

RAG_API_TOKEN = (os.getenv("RAG_API_TOKEN") or os.getenv("API_TOKEN") or "").strip()
RAG_REQUIRE_TOKEN = os.getenv("RAG_REQUIRE_TOKEN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

if RAG_REQUIRE_TOKEN and not RAG_API_TOKEN:
    raise RuntimeError(
        "RAG token is required but missing. "
        "Set RAG_API_TOKEN/API_TOKEN or disable with RAG_REQUIRE_TOKEN=false."
    )


async def _run_build(
    planner: RAGReasoningPlanner,
    agent: RAGBuildAgent,
    ingestion_service: KnowledgeIngestionService | None,
    pdf_path: Path,
    outdir: Path,
    push: bool,
    overrides: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()

    def _worker() -> Dict[str, Any]:
        build = agent.build_bundle(pdf_path=pdf_path, outdir=outdir, overrides=overrides or {})
        ingest_result = None
        if push and ingestion_service is not None:
            payload = episode_from_rag_artifacts(pdf_path, build.artifacts)
            ingest_result = ingestion_service.ingest_episode(payload)
        return {
            "plan": build.plan.__dict__,
            "artifacts": build.artifacts,
            "ingested": asdict(ingest_result) if ingest_result else None,
        }

    return await loop.run_in_executor(None, _worker)


def create_app() -> web.Application:
    app = web.Application()
    planner = RAGReasoningPlanner()
    agent = RAGBuildAgent(planner=planner)
    ingestion_service = KnowledgeIngestionService()

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if RAG_REQUIRE_TOKEN:
            provided = extract_auth_token(request.headers)
            if not provided:
                return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
            if not token_matches(RAG_API_TOKEN, provided):
                return web.json_response({"ok": False, "error": "Forbidden"}, status=403)
        return await handler(request)

    app.middlewares.append(auth_middleware)

    async def build_handler(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)

        pdf_raw = data.get("pdf_path")
        if not pdf_raw:
            return web.json_response({"ok": False, "error": "Missing pdf_path"}, status=400)
        pdf_path = Path(pdf_raw).expanduser().resolve()
        if not pdf_path.exists():
            return web.json_response({"ok": False, "error": f"PDF not found: {pdf_path}"}, status=404)

        outdir_raw = data.get("outdir", "/app/data/output")
        outdir = Path(outdir_raw).expanduser().resolve()
        outdir.mkdir(parents=True, exist_ok=True)

        push = bool(data.get("push", False))
        overrides = data.get("overrides")

        result = await _run_build(planner, agent, ingestion_service, pdf_path, outdir, push, overrides)
        return web.json_response({"ok": True, **result})

    app.router.add_post("/build", build_handler)
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
