from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from dataclasses import asdict

from .kg_pipeline import KnowledgeIngestionService, episode_from_rag_artifacts
from .profile_namespace import (
    build_profile_output_dir,
    compose_group_id,
    normalize_profile_id,
    safe_doc_slug,
)
from .rag_reasoning import RAGBuildAgent, RAGReasoningPlanner
from .security import (
    RateLimiter,
    SECURITY_HEADERS,
    extract_client_ip,
    extract_auth_token,
    is_path_within_roots,
    parse_allowed_roots,
    parse_trusted_proxy_networks,
    token_matches,
)

RAG_API_TOKEN = (os.getenv("RAG_API_TOKEN") or os.getenv("API_TOKEN") or "").strip()
RAG_REQUIRE_TOKEN = os.getenv("RAG_REQUIRE_TOKEN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TRUSTED_PROXY_CIDRS = parse_trusted_proxy_networks(os.getenv("TRUSTED_PROXY_CIDRS"))
RAG_RATE_LIMIT_PER_MINUTE = int(os.getenv("RAG_RATE_LIMIT_PER_MINUTE", "30"))
_DEFAULT_INPUT_ROOTS = (
    Path("/app/data"),
    Path("data").resolve(),
)
_DEFAULT_OUTPUT_ROOTS = (
    Path("/app/data/output"),
    Path("data/output").resolve(),
)
RAG_ALLOWED_INPUT_ROOTS = parse_allowed_roots(
    os.getenv("RAG_ALLOWED_INPUT_ROOTS"),
    _DEFAULT_INPUT_ROOTS,
)
RAG_ALLOWED_OUTPUT_ROOTS = parse_allowed_roots(
    os.getenv("RAG_ALLOWED_OUTPUT_ROOTS"),
    _DEFAULT_OUTPUT_ROOTS,
)
ALLOWED_OVERRIDE_KEYS = {"chunk_size", "overlap", "max_sections", "triples"}

if RAG_REQUIRE_TOKEN and not RAG_API_TOKEN:
    raise RuntimeError(
        "RAG token is required but missing. Set RAG_API_TOKEN/API_TOKEN or disable with RAG_REQUIRE_TOKEN=false."
    )


async def _run_build(
    planner: RAGReasoningPlanner,
    agent: RAGBuildAgent,
    ingestion_service: KnowledgeIngestionService | None,
    pdf_path: Path,
    outdir: Path,
    push: bool,
    overrides: Optional[Dict[str, Any]],
    profile_id: str | None = None,
) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()

    def _worker() -> Dict[str, Any]:
        build = agent.build_bundle(pdf_path=pdf_path, outdir=outdir, overrides=overrides or {})
        ingest_result = None
        if push and ingestion_service is not None:
            group_id = (
                compose_group_id(profile_id, "rag_build", safe_doc_slug(pdf_path.stem))
                if profile_id
                else None
            )
            payload = episode_from_rag_artifacts(
                pdf_path,
                build.artifacts,
                group_id=group_id,
                profile_id=profile_id,
            )
            ingest_result = ingestion_service.ingest_episode(payload)
        return {
            "plan": build.plan.__dict__,
            "artifacts": build.artifacts,
            "ingested": asdict(ingest_result) if ingest_result else None,
        }

    return await loop.run_in_executor(None, _worker)


def create_app() -> web.Application:
    app = web.Application(client_max_size=1024 * 1024)
    planner = RAGReasoningPlanner()
    agent = RAGBuildAgent(planner=planner)
    ingestion_service = KnowledgeIngestionService()
    limiter = RateLimiter(max_requests=RAG_RATE_LIMIT_PER_MINUTE, window_seconds=60)

    @web.middleware
    async def security_headers_middleware(request: web.Request, handler):
        response = await handler(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if RAG_API_TOKEN:
            provided = extract_auth_token(request.headers)
            if not provided:
                return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
            if not token_matches(RAG_API_TOKEN, provided):
                return web.json_response({"ok": False, "error": "Forbidden"}, status=403)
        return await handler(request)

    @web.middleware
    async def rate_limit_middleware(request: web.Request, handler):
        client_key = extract_client_ip(
            request.remote,
            request.headers.get("X-Forwarded-For"),
            TRUSTED_PROXY_CIDRS,
        )
        allowed, retry_after = limiter.allow(client_key)
        if not allowed:
            return web.json_response(
                {"ok": False, "error": "Rate limit exceeded"},
                status=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await handler(request)

    app.middlewares.extend([security_headers_middleware, rate_limit_middleware, auth_middleware])

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
        if not is_path_within_roots(pdf_path, RAG_ALLOWED_INPUT_ROOTS):
            return web.json_response({"ok": False, "error": "PDF path is not under allowed roots"}, status=403)

        outdir_raw = data.get("outdir", "/app/data/output")
        outdir = Path(outdir_raw).expanduser().resolve()
        if not is_path_within_roots(outdir, RAG_ALLOWED_OUTPUT_ROOTS):
            return web.json_response({"ok": False, "error": "Output path is not under allowed roots"}, status=403)
        profile_raw = data.get("profile_id") or data.get("profile")
        profile_id = normalize_profile_id(str(profile_raw)) if profile_raw else None
        if profile_id:
            outdir = build_profile_output_dir(outdir, profile_id, "rag_build")
        outdir.mkdir(parents=True, exist_ok=True)

        push = bool(data.get("push", False))
        overrides = data.get("overrides")
        if overrides is not None and not isinstance(overrides, dict):
            return web.json_response({"ok": False, "error": "'overrides' must be an object"}, status=400)
        if isinstance(overrides, dict):
            unknown = sorted(set(overrides.keys()) - ALLOWED_OVERRIDE_KEYS)
            if unknown:
                return web.json_response(
                    {"ok": False, "error": f"Unsupported override keys: {', '.join(unknown)}"},
                    status=400,
                )

        result = await _run_build(
            planner,
            agent,
            ingestion_service,
            pdf_path,
            outdir,
            push,
            overrides,
            profile_id=profile_id,
        )
        return web.json_response({"ok": True, **result})

    app.router.add_post("/build", build_handler)
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8100)


if __name__ == "__main__":
    main()
