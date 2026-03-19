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
import re
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext, Tool

from .cag_agent import CAGAgent
from .config import REASON_MODEL
from .domain_profile_agent import DomainProfileAgent, DomainWizardRequest as DomainWizardAgentRequest
from .profile_catalog import ProfileCatalogService
from .profile_cleanup import cleanup_profile_local_artifacts
from .profile_namespace import (
    build_profile_output_dir,
    compose_group_id,
    get_active_profile_file,
    normalize_profile_id,
    read_active_profile,
    resolve_profile_id,
    safe_doc_slug,
    write_active_profile,
)
from .rag_reasoning import RAGBuildAgent
from .rag_builder_core import ensure_dir, read_pdf_text_blocks
from .web_research_agent import WebResearchAgent
from .cag_agent import extract_text_from_file
from .vision_agent import run_capture_once, run_image_once
from .prompt_loader import load_required_prompt
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
from .settings import SettingsError, get_settings


app = FastAPI(title="Study Agents Copilot Service")
COPILOT_API_KEY = (os.getenv("COPILOT_API_KEY") or os.getenv("API_TOKEN") or "").strip()
COPILOT_REQUIRE_TOKEN = os.getenv("COPILOT_REQUIRE_TOKEN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TRUSTED_PROXY_CIDRS = parse_trusted_proxy_networks(os.getenv("TRUSTED_PROXY_CIDRS"))
COPILOT_RATE_LIMIT_PER_MINUTE = int(os.getenv("COPILOT_RATE_LIMIT_PER_MINUTE", "120"))
COPILOT_MAX_BODY_BYTES = int(os.getenv("COPILOT_MAX_BODY_BYTES", "524288"))
COPILOT_MAX_CAPTURE_IMAGE_BYTES = int(
    os.getenv("COPILOT_MAX_CAPTURE_IMAGE_BYTES", "10485760")
)
_DEFAULT_ALLOWED_FILE_ROOTS = (
    Path("/app/data"),
    Path("/app/research_output"),
    Path("data").resolve(),
    Path("research_output").resolve(),
)
COPILOT_ALLOWED_FILE_ROOTS = parse_allowed_roots(
    os.getenv("COPILOT_ALLOWED_FILE_ROOTS"),
    _DEFAULT_ALLOWED_FILE_ROOTS,
)
_copilot_rate_limiter = RateLimiter(COPILOT_RATE_LIMIT_PER_MINUTE, window_seconds=60)
DEFAULT_RESEARCH_OUTPUT_DIR = Path(
    os.getenv("COPILOT_RESEARCH_OUTPUT_DIR", "/app/data/output/research")
).expanduser()
COPILOT_ORCHESTRATOR_PROMPT = load_required_prompt(
    "copilot_orchestrator_system.txt"
)
_MODEL_PREFIXES = {
    "anthropic",
    "bedrock",
    "google",
    "groq",
    "mistral",
    "ollama",
    "openai",
    "vertexai",
}
COPILOT_REQUIRE_PROFILE_SCHEMA = (
    os.getenv("COPILOT_REQUIRE_PROFILE_SCHEMA", "true").strip().lower()
    in {"1", "true", "yes", "on"}
)
COPILOT_ENABLE_PROFILE_PURGE = (
    os.getenv("COPILOT_ENABLE_PROFILE_PURGE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)
COPILOT_ENABLE_PROFILE_DELETE = (
    os.getenv("COPILOT_ENABLE_PROFILE_DELETE", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

if COPILOT_REQUIRE_TOKEN and not COPILOT_API_KEY:
    raise RuntimeError(
        "Copilot API token is required but missing. Set COPILOT_API_KEY/API_TOKEN or disable with COPILOT_REQUIRE_TOKEN=false."
    )


class ChatRequest(BaseModel):
    message: str
    profile_id: Optional[str] = None


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
    platform: Optional[str] = None
    model: Optional[str] = None
    ollama_target: Optional[str] = None
    profile_id: Optional[str] = None


class PrepareMarkdownRequest(BaseModel):
    path: str
    profile_id: Optional[str] = None


class CagProcessRequest(BaseModel):
    path: Optional[str] = None
    message: Optional[str] = None
    profile_id: Optional[str] = None


class ProfileUpsertRequest(BaseModel):
    profile_id: str
    name: Optional[str] = None
    summary: Optional[str] = None
    prompt_profile_name: Optional[str] = None
    tags: list[str] = []
    status: str = "active"


class ProfileUseRequest(BaseModel):
    profile_id: str


class ProfilePurgeRequest(BaseModel):
    profile_id: str
    dry_run: bool = True
    include_artifacts: bool = False
    confirm_text: Optional[str] = None


class ProfileDeleteRequest(BaseModel):
    profile_id: str
    dry_run: bool = True
    confirm_text: Optional[str] = None


class DomainWizardRequest(BaseModel):
    profile_name: str
    domain_seed: Optional[str] = None
    quickstart: bool = True
    apply: bool = True
    check: bool = True
    use_ai: bool = True
    targets: str = "entity,edge,cag_entity,cag_relationship,vision,cag_answer,scenario_structurer,scenario_context"
    platform: Optional[str] = None
    ai_model: Optional[str] = None
    ollama_target: Optional[str] = None
    ai_temperature: float = 0.2
    no_ai_fallback: bool = False
    timeout_seconds: int = 600
    rollback_on_error: bool = True
    profile_id: Optional[str] = None


async def require_api_key(x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
    if not COPILOT_REQUIRE_TOKEN:
        return
    provided = extract_auth_token({"X-API-Key": x_api_key or "", "Authorization": authorization or ""})
    if not provided:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not token_matches(COPILOT_API_KEY, provided):
        raise HTTPException(status_code=403, detail="Forbidden")


def _ensure_allowed_file_path(path_value: str) -> Path:
    resolved = Path(path_value).expanduser().resolve()
    if not is_path_within_roots(resolved, COPILOT_ALLOWED_FILE_ROOTS):
        raise ValueError("Path is not under allowed roots")
    return resolved


def _profile_catalog() -> ProfileCatalogService:
    return ProfileCatalogService()


def _run_domain_wizard(req: DomainWizardRequest, resolved_profile: str | None = None) -> dict[str, Any]:
    profile_name = normalize_profile_id(req.profile_name)
    summary = (
        f"Prompt profile managed by domain wizard for {req.domain_seed.strip()}"
        if req.domain_seed and req.domain_seed.strip()
        else f"Prompt profile managed by domain wizard for {profile_name}"
    )
    catalog = _profile_catalog()
    profile_id = _canonical_profile(_resolved_profile(resolved_profile or req.profile_id or profile_name))
    catalog.ensure_profile(
        profile_id,
        name=profile_id,
        summary=summary,
        prompt_profile_name=profile_name,
        tags=["user_created", "domain_wizard"],
    )

    timeout = max(30, min(req.timeout_seconds, 1800))
    agent_req = DomainWizardAgentRequest(
        profile_name=profile_name,
        domain_seed=req.domain_seed,
        quickstart=req.quickstart,
        apply=req.apply,
        check=req.check,
        use_ai=req.use_ai,
        targets=req.targets,
        env_file=".env",
        platform=req.platform,
        ai_model=req.ai_model,
        ollama_target=req.ollama_target,
        ai_temperature=req.ai_temperature,
        no_ai_fallback=req.no_ai_fallback,
        timeout_seconds=timeout,
        rollback_on_error=req.rollback_on_error,
    )
    result = DomainProfileAgent().run(agent_req)
    prompt_rows = [
        {"path": row.path, "lines": row.lines, "sha256": row.sha256}
        for row in result.prompt_files
    ]

    metadata = {
        "exit_code": result.exit_code,
        "generated_targets": result.generated_targets,
        "prompt_files": prompt_rows,
        "rolled_back": result.rolled_back,
        "profile_path": result.profile_path,
    }
    if result.ok:
        artifact_path = result.profile_path or f"domain/profiles/{profile_name}.json"
        catalog.record_artifact(
            profile_id=profile_id,
            agent="domain_profile_agent",
            artifact_type="prompt_profile_bundle",
            path=artifact_path,
            run_id=uuid.uuid4().hex[:8],
            source_ids=[profile_name],
            metadata=metadata,
        )

    return {
        "ok": result.ok,
        "profile_id": profile_id,
        "prompt_profile_name": profile_name,
        "exit_code": result.exit_code,
        "command": result.command,
        "profile_path": result.profile_path,
        "generated_targets": result.generated_targets,
        "prompt_files": prompt_rows,
        "rolled_back": result.rolled_back,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


@app.on_event("startup")
async def validate_profile_schema_startup() -> None:
    if not COPILOT_REQUIRE_PROFILE_SCHEMA:
        return
    try:
        _profile_catalog().list_profiles(limit=1, include_inactive=True)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Profile schema validation failed. Run supabase_schema.sql or "
            "supabase/migrations/202603150001_profile_catalog.sql."
        ) from exc


def _resolved_profile(explicit: str | None = None) -> str:
    return resolve_profile_id(explicit, allow_default=True) or "default"


def _canonical_profile(profile_id: str) -> str:
    try:
        return _profile_catalog().resolve_alias(profile_id)
    except Exception:
        return profile_id


def _expected_profile_purge_confirm(profile_id: str) -> str:
    return f"PURGE {normalize_profile_id(profile_id)}"


def _expected_profile_delete_confirm(profile_id: str) -> str:
    return f"DELETE PROFILE {normalize_profile_id(profile_id)}"


def _resolve_profile_output_dir(profile_id: str, agent_name: str) -> Path:
    """
    Return a writable profile+agent output directory for artifacts.
    """
    scoped = build_profile_output_dir(DEFAULT_RESEARCH_OUTPUT_DIR, profile_id, agent_name)
    try:
        scoped.mkdir(parents=True, exist_ok=True)
        probe = scoped / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Research output directory is not writable: {scoped}"
        ) from exc
    return scoped


def _load_profile_graph_rows(
    profile_id: str,
    *,
    max_scan_nodes: int = 2000,
    max_scan_edges: int = 4000,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    catalog = _profile_catalog()
    canonical = catalog.resolve_alias(profile_id)
    client = catalog.client
    prefix = f"profile:{canonical}:%"

    nodes: list[dict[str, Any]] = []
    try:
        rows = (
            client.table("kg_nodes")
            .select("id,title,type,group_id,profile_id")
            .eq("profile_id", canonical)
            .limit(max_scan_nodes)
            .execute()
            .data
        ) or []
        nodes = rows if isinstance(rows, list) else []
    except Exception:
        nodes = []
    if not nodes:
        try:
            rows = (
                client.table("kg_nodes")
                .select("id,title,type,group_id,profile_id")
                .like("group_id", prefix)
                .limit(max_scan_nodes)
                .execute()
                .data
            ) or []
            nodes = rows if isinstance(rows, list) else []
        except Exception:
            nodes = []

    node_map: dict[str, dict[str, Any]] = {}
    for row in nodes:
        node_id = str(row.get("id") or "").strip()
        if not node_id:
            continue
        if node_id in node_map:
            continue
        node_map[node_id] = {
            "id": node_id,
            "title": str(row.get("title") or node_id).strip() or node_id,
            "type": str(row.get("type") or "Node").strip() or "Node",
        }

    edges: list[dict[str, Any]] = []
    try:
        rows = (
            client.table("kg_edges")
            .select("src,dst,rel,group_id,profile_id")
            .eq("profile_id", canonical)
            .limit(max_scan_edges)
            .execute()
            .data
        ) or []
        edges = rows if isinstance(rows, list) else []
    except Exception:
        edges = []
    if not edges:
        try:
            rows = (
                client.table("kg_edges")
                .select("src,dst,rel,group_id,profile_id")
                .like("group_id", prefix)
                .limit(max_scan_edges)
                .execute()
                .data
            ) or []
            edges = rows if isinstance(rows, list) else []
        except Exception:
            edges = []

    node_ids = set(node_map.keys())
    shaped_edges: list[dict[str, Any]] = []
    for row in edges:
        src = str(row.get("src") or "").strip()
        dst = str(row.get("dst") or "").strip()
        if not src or not dst:
            continue
        if src not in node_ids or dst not in node_ids:
            continue
        shaped_edges.append(
            {
                "src": src,
                "dst": dst,
                "rel": str(row.get("rel") or "related_to").strip() or "related_to",
            }
        )
    return list(node_map.values()), shaped_edges


def _filter_profile_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    query: str,
    *,
    max_nodes: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    trimmed = (query or "").strip().lower()
    tokens = []
    if trimmed:
        raw_tokens = re.sub(r"[^a-z0-9\s_-]+", " ", trimmed).split()
        seen: set[str] = set()
        for token in raw_tokens:
            token = token.strip()
            if len(token) < 3 or token in seen:
                continue
            seen.add(token)
            tokens.append(token)

    if not tokens:
        subset_nodes = nodes[:max_nodes]
        node_ids = {str(item.get("id") or "") for item in subset_nodes}
        subset_edges = [
            edge
            for edge in edges
            if str(edge.get("src") or "") in node_ids and str(edge.get("dst") or "") in node_ids
        ][:max_edges]
        return subset_nodes, subset_edges, len(subset_nodes)

    scored: list[tuple[int, dict[str, Any]]] = []
    for node in nodes:
        haystack = " ".join(
            [
                str(node.get("id") or "").lower(),
                str(node.get("title") or "").lower(),
                str(node.get("type") or "").lower(),
            ]
        )
        score = 0
        for token in tokens:
            if token in haystack:
                score += 1
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        subset_nodes = nodes[:max_nodes]
        node_ids = {str(item.get("id") or "") for item in subset_nodes}
        subset_edges = [
            edge
            for edge in edges
            if str(edge.get("src") or "") in node_ids and str(edge.get("dst") or "") in node_ids
        ][:max_edges]
        return subset_nodes, subset_edges, 0

    matched_nodes = [item[1] for item in scored]
    selected = {str(node.get("id") or "") for node in matched_nodes[:max_nodes]}
    selected.discard("")
    matched_count = len(matched_nodes)

    for edge in edges:
        if len(selected) >= max_nodes:
            break
        src = str(edge.get("src") or "")
        dst = str(edge.get("dst") or "")
        if src in selected or dst in selected:
            if src:
                selected.add(src)
            if dst:
                selected.add(dst)

    subset_nodes = [node for node in nodes if str(node.get("id") or "") in selected][:max_nodes]
    subset_ids = {str(node.get("id") or "") for node in subset_nodes}
    subset_edges = [
        edge
        for edge in edges
        if str(edge.get("src") or "") in subset_ids and str(edge.get("dst") or "") in subset_ids
    ][:max_edges]
    return subset_nodes, subset_edges, matched_count


def _prepare_markdown_from_path(source_path: Path, target_dir: Path | None = None) -> Path:
    suffix = source_path.suffix.lower()
    if suffix == ".md":
        if target_dir is None:
            return source_path
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{safe_doc_slug(source_path.stem)}.md"
        if not is_path_within_roots(target_path, COPILOT_ALLOWED_FILE_ROOTS):
            raise ValueError("Output path is not under allowed roots")
        target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        return target_path

    if target_dir is None:
        target_path = source_path.with_suffix(".md")
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{safe_doc_slug(source_path.stem)}.md"
    if not is_path_within_roots(target_path, COPILOT_ALLOWED_FILE_ROOTS):
        raise ValueError("Output path is not under allowed roots")

    if suffix == ".pdf":
        pages = read_pdf_text_blocks(source_path)
        lines: list[str] = [f"# {source_path.stem}", "", f"_Prepared from {source_path.name}_", ""]
        for page in pages:
            lines.append(f"## Page {page.get('page', '?')}")
            lines.append("")
            lines.append((page.get("text") or "").strip())
            lines.append("")
        markdown_text = "\n".join(lines).rstrip() + "\n"
    else:
        text = extract_text_from_file(str(source_path))
        markdown_text = f"# {source_path.stem}\n\n{text.strip()}\n"

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(markdown_text, encoding="utf-8")
    return target_path


@app.middleware("http")
async def copilot_security_middleware(request: Request, call_next):
    max_body_bytes = (
        COPILOT_MAX_CAPTURE_IMAGE_BYTES
        if request.url.path.endswith("/copilot/capture-image")
        else COPILOT_MAX_BODY_BYTES
    )
    content_length_raw = request.headers.get("content-length")
    try:
        content_length = int(content_length_raw) if content_length_raw else 0
    except ValueError:
        content_length = 0
    if content_length and content_length > max_body_bytes:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)

    client_key = extract_client_ip(
        request.client.host if request.client else None,
        request.headers.get("X-Forwarded-For"),
        TRUSTED_PROXY_CIDRS,
    )
    allowed, retry_after = _copilot_rate_limiter.allow(client_key)
    if not allowed:
        return JSONResponse(
            {"detail": "Rate limit exceeded"},
            status_code=429,
            headers={"Retry-After": str(retry_after)},
        )

    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


def _build_tools():
    cag = CAGAgent()
    rag_agent = RAGBuildAgent()

    def _ctx_profile(ctx: RunContext[dict], explicit: Optional[str] = None) -> str:
        dep_profile = None
        if isinstance(getattr(ctx, "deps", None), dict):
            dep_profile = ctx.deps.get("profile_id")
        resolved = _resolved_profile(explicit or dep_profile)
        canonical = _canonical_profile(resolved)
        _profile_catalog().ensure_profile(canonical, name=canonical)
        return canonical

    @Tool
    def ask_question(ctx: RunContext[dict], question: str) -> dict:
        """
        Answer a question using the CAG pipeline with enhanced retrieval.
        """
        profile_id = _ctx_profile(ctx)
        context = cag.enhanced_retrieve_context(question, profile_id=profile_id)
        answer = cag._generate_answer_with_context(question, context)
        return {"answer": answer, "context": context, "profile_id": profile_id}

    @Tool
    def rag_build(
        ctx: RunContext[dict],
        pdf_path: str,
        outdir: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> dict:
        """
        Build a RAG bundle for a PDF or Markdown file. Returns artifact paths; does not push to Supabase.
        """
        resolved_profile = _ctx_profile(ctx, profile_id)
        source_path = _ensure_allowed_file_path(pdf_path)
        if outdir:
            outdir_path = Path(outdir).expanduser().resolve()
            if not is_path_within_roots(outdir_path, COPILOT_ALLOWED_FILE_ROOTS):
                raise ValueError("Output path is not under allowed roots")
        else:
            run_id = uuid.uuid4().hex[:8]
            outdir_path = _resolve_profile_output_dir(
                resolved_profile, "rag_build"
            ) / run_id
        outdir_path = ensure_dir(outdir_path)
        result = rag_agent.build_bundle(pdf_path=source_path, outdir=outdir_path)
        _profile_catalog().record_artifact(
            profile_id=resolved_profile,
            agent="rag_build",
            artifact_type="rag_bundle",
            path=str(result.artifacts.get("folder") or outdir_path),
            run_id=safe_doc_slug(source_path.stem),
            source_ids=[str(source_path)],
            metadata={"artifacts": result.artifacts},
        )
        return {
            "artifacts": result.artifacts,
            "plan": asdict(result.plan),
            "profile_id": resolved_profile,
        }

    @Tool
    async def web_research(
        ctx: RunContext[dict],
        url: str,
        max_depth: int = 2,
        max_pages: int = 10,
        query: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> dict:
        """
        Run the web research agent against a URL. Returns the output markdown path and stats.
        """
        resolved_profile = _ctx_profile(ctx, profile_id)
        run_id = uuid.uuid4().hex[:8]
        output_dir = _resolve_profile_output_dir(
            resolved_profile, "web_research"
        ) / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        agent = WebResearchAgent(
            output_dir=output_dir,
            query=query or "",
            use_llm_relevance=False,
            download_docs=False,
            auto_ingest=False,
            ingest_threshold=0.6,
            ingest_group=compose_group_id(resolved_profile, "web_research"),
            ingest_chunk_size=1200,
            ingest_overlap=150,
            resume_file=None,
            resume_reset=False,
            profile_id=resolved_profile,
            agent_namespace="web_research",
        )
        results = await agent.research_from_url(url, max_depth=max_depth, max_pages=max_pages)
        rag_path = agent.prepare_rag_content(results)
        _profile_catalog().record_artifact(
            profile_id=resolved_profile,
            agent="web_research",
            artifact_type="research_markdown",
            path=str(rag_path),
            run_id=run_id,
            source_ids=[url],
            metadata={"pages": len(results), "output_dir": str(output_dir)},
        )
        return {
            "pages": len(results),
            "average_relevance": (
                sum(r.relevance_score for r in results) / len(results) if results else 0.0
            ),
            "output_dir": str(output_dir),
            "rag_markdown": str(rag_path),
            "profile_id": resolved_profile,
        }

    @Tool
    def cag_process(
        ctx: RunContext[dict], file_path: str, profile_id: Optional[str] = None
    ) -> dict:
        """
        Run the CAG pipeline on a file (PDF or markdown) and ingest into Supabase.
        """
        resolved_profile = _ctx_profile(ctx, profile_id)
        safe_path = _ensure_allowed_file_path(file_path)
        text = extract_text_from_file(str(safe_path))
        result = cag.process_document_with_cag(
            text, source=str(safe_path), profile_id=resolved_profile
        )
        _profile_catalog().record_artifact(
            profile_id=resolved_profile,
            agent="cag_process",
            artifact_type="cag_ingestion",
            path=str(safe_path),
            run_id=safe_doc_slug(safe_path.stem),
            source_ids=[str(safe_path)],
            metadata=result,
        )
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

    @Tool
    def domain_profile_wizard(
        ctx: RunContext[dict],
        profile_name: str,
        domain_seed: Optional[str] = None,
        apply: bool = True,
        check: bool = True,
        use_ai: bool = True,
        platform: Optional[str] = None,
        ai_model: Optional[str] = None,
    ) -> dict:
        """
        Run the domain profile wizard as a control-plane agent to generate prompt files.
        """
        req = DomainWizardRequest(
            profile_name=profile_name,
            domain_seed=domain_seed,
            quickstart=True,
            apply=apply,
            check=check,
            use_ai=use_ai,
            platform=platform,
            ai_model=ai_model,
        )
        profile_id = _ctx_profile(ctx, profile_name)
        return _run_domain_wizard(req, resolved_profile=profile_id)

    return [ask_question, rag_build, web_research, cag_process, vision_capture, domain_profile_wizard]


def _resolve_copilot_agent_model() -> str:
    configured = (os.getenv("COPILOT_AGENT_MODEL") or "").strip()
    if configured:
        return configured

    provider = (os.getenv("COPILOT_AGENT_PROVIDER") or "").strip().lower()
    if provider:
        return f"{provider}:{REASON_MODEL}"

    if ":" in REASON_MODEL:
        prefix = REASON_MODEL.split(":", 1)[0].strip().lower()
        if prefix in _MODEL_PREFIXES:
            return REASON_MODEL
        if (os.getenv("OLLAMA_BASE_URL") or "").strip():
            return f"ollama:{REASON_MODEL}"
        fallback_openai = (
            os.getenv("COPILOT_FALLBACK_OPENAI_MODEL")
            or os.getenv("OPENAI_FALLBACK_MODEL")
            or "gpt-4o-mini"
        ).strip()
        return f"openai:{fallback_openai}"

    return f"openai:{REASON_MODEL}"


def _build_agent() -> Agent[dict]:
    return Agent(
        model=_resolve_copilot_agent_model(),
        tools=_build_tools(),
        output_type=str,
        system_prompt=COPILOT_ORCHESTRATOR_PROMPT,
    )


agent = _build_agent()


@app.post("/copilot/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key)])
async def copilot_chat(
    req: ChatRequest, x_profile_id: str | None = Header(default=None, alias="X-Profile-ID")
) -> ChatResponse:
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    profile_id = _canonical_profile(_resolved_profile(req.profile_id or x_profile_id))

    try:
        result = await agent.run(req.message, deps={"profile_id": profile_id})
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
    traces["profile_id"] = profile_id
    return ChatResponse(reply=reply, traces=traces)


@app.get("/profiles", dependencies=[Depends(require_api_key)])
async def list_profiles(limit: int = 100, include_inactive: bool = False, include_inferred: bool = False):
    try:
        catalog = _profile_catalog()
        active_profile = read_active_profile()
        profiles = catalog.list_profiles(
            limit=limit,
            include_inactive=include_inactive,
            include_inferred=include_inferred,
            active_profile_id=active_profile,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "profiles": profiles,
        "active_profile_id": active_profile,
    }


@app.get("/profiles/{profile_id}", dependencies=[Depends(require_api_key)])
async def get_profile(profile_id: str):
    try:
        profile = _profile_catalog().get_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return profile


@app.get("/profiles/{profile_id}/graph", dependencies=[Depends(require_api_key)])
async def get_profile_graph(
    profile_id: str,
    query: str = "",
    max_nodes: int = 140,
    max_edges: int = 260,
):
    try:
        canonical = _canonical_profile(_resolved_profile(profile_id))
        _profile_catalog().ensure_profile(canonical, name=canonical)
        nodes, edges = _load_profile_graph_rows(canonical)
        safe_max_nodes = max(20, min(max_nodes, 300))
        safe_max_edges = max(20, min(max_edges, 600))
        filtered_nodes, filtered_edges, matched_count = _filter_profile_graph(
            nodes,
            edges,
            query,
            max_nodes=safe_max_nodes,
            max_edges=safe_max_edges,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "profile_id": canonical,
        "query": query,
        "nodes": filtered_nodes,
        "edges": filtered_edges,
        "counts": {
            "nodes": len(filtered_nodes),
            "edges": len(filtered_edges),
            "matched_nodes": matched_count,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
        },
    }


@app.post("/profiles", dependencies=[Depends(require_api_key)])
async def upsert_profile(req: ProfileUpsertRequest):
    try:
        tags = list(req.tags or [])
        if "user_created" not in {str(t).strip().lower() for t in tags}:
            tags.append("user_created")
        profile = _profile_catalog().ensure_profile(
            req.profile_id,
            name=req.name,
            summary=req.summary,
            prompt_profile_name=req.prompt_profile_name,
            tags=tags,
            status=req.status,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "profile": profile}


@app.post("/profiles/use", dependencies=[Depends(require_api_key)])
async def use_profile(req: ProfileUseRequest):
    try:
        canonical = _profile_catalog().resolve_alias(req.profile_id)
        profile = _profile_catalog().ensure_profile(
            canonical,
            name=canonical,
            tags=["user_created"],
        )
        state_path = write_active_profile(profile["profile_id"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": True,
        "active_profile_id": profile["profile_id"],
        "state_path": str(state_path),
    }


@app.post("/profiles/purge", dependencies=[Depends(require_api_key)])
async def purge_profile(req: ProfilePurgeRequest):
    if not COPILOT_ENABLE_PROFILE_PURGE:
        raise HTTPException(
            status_code=403,
            detail=(
                "Profile purge is disabled. Set COPILOT_ENABLE_PROFILE_PURGE=true "
                "to enable this endpoint."
            ),
        )

    expected_confirm_text = _expected_profile_purge_confirm(req.profile_id)
    if not req.dry_run:
        provided = (req.confirm_text or "").strip()
        if provided != expected_confirm_text:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Confirmation text mismatch. Re-submit with "
                    f"confirm_text='{expected_confirm_text}'."
                ),
            )

    catalog = _profile_catalog()
    try:
        report = catalog.purge_profile_data(
            req.profile_id,
            dry_run=req.dry_run,
            include_artifacts=req.include_artifacts,
        )
        if not req.dry_run:
            catalog.record_artifact(
                profile_id=report["profile_id"],
                agent="copilot_service",
                artifact_type="profile_purge_audit",
                path=f"supabase://profiles/{report['profile_id']}/purge",
                metadata={
                    "requested_profile_id": report["requested_profile_id"],
                    "alias_resolved": report["alias_resolved"],
                    "include_artifacts": req.include_artifacts,
                    "summary": report["summary"],
                    "tables": report["tables"],
                },
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "expected_confirm_text": expected_confirm_text,
        **report,
    }


@app.post("/profiles/delete", dependencies=[Depends(require_api_key)])
async def delete_profile(req: ProfileDeleteRequest):
    if not COPILOT_ENABLE_PROFILE_DELETE:
        raise HTTPException(
            status_code=403,
            detail=(
                "Profile delete is disabled. Set COPILOT_ENABLE_PROFILE_DELETE=true "
                "to enable this endpoint."
            ),
        )

    expected_confirm_text = _expected_profile_delete_confirm(req.profile_id)
    if not req.dry_run:
        provided = (req.confirm_text or "").strip()
        if provided != expected_confirm_text:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Confirmation text mismatch. Re-submit with "
                    f"confirm_text='{expected_confirm_text}'."
                ),
            )

    catalog = _profile_catalog()
    try:
        db_report = catalog.delete_profile_everything(
            req.profile_id,
            dry_run=req.dry_run,
        )
        local_report = cleanup_profile_local_artifacts(
            db_report["profile_id"],
            prompt_profile_name=db_report.get("prompt_profile_name"),
            dry_run=req.dry_run,
        )
        active_profile = read_active_profile()
        active_profile_cleared = False
        if (
            not req.dry_run
            and active_profile
            and normalize_profile_id(active_profile) == db_report["profile_id"]
        ):
            state_path = get_active_profile_file()
            state_path.unlink(missing_ok=True)
            active_profile_cleared = True
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "expected_confirm_text": expected_confirm_text,
        "active_profile_cleared": active_profile_cleared if not req.dry_run else False,
        "db_report": db_report,
        "local_report": local_report,
    }


@app.post("/domain/wizard", dependencies=[Depends(require_api_key)])
async def run_domain_wizard(req: DomainWizardRequest):
    try:
        result = _run_domain_wizard(req)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not result["ok"]:
        raise HTTPException(status_code=422, detail=result)
    return result


@app.get("/domain/wizard/history", dependencies=[Depends(require_api_key)])
async def domain_wizard_history(profile_id: str, limit: int = 50):
    try:
        return _profile_catalog().list_domain_wizard_history(profile_id, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/copilot/capture", dependencies=[Depends(require_api_key)])
async def copilot_capture(
    req: CaptureRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-ID"),
):
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    try:
        resolved_profile = _canonical_profile(_resolved_profile(req.profile_id or x_profile_id))
        _profile_catalog().ensure_profile(resolved_profile, name=resolved_profile)
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
            platform=req.platform,
            model=req.model,
            ollama_target=req.ollama_target,
            profile_id=resolved_profile,
        )
        if isinstance(result, dict):
            result["profile_id"] = resolved_profile
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


@app.post("/copilot/capture-image", dependencies=[Depends(require_api_key)])
async def copilot_capture_image(
    request: Request,
    image: UploadFile = File(...),
    mode: str = Form("local"),
    remote_cag_url: Optional[str] = Form(default=None),
    remote_image_url: Optional[str] = Form(default=None),
    remote_mcp_url: Optional[str] = Form(default=None),
    platform: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    ollama_target: Optional[str] = Form(default=None),
    profile_id: Optional[str] = Form(default=None),
    x_profile_id: str | None = Header(default=None, alias="X-Profile-ID"),
):
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    content_type = (image.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")

    # Work around inconsistent multipart field binding by reading raw form values.
    # This ensures platform/model overrides from the UI are respected.
    form = await request.form()

    def _form_text(name: str, current: Optional[str]) -> Optional[str]:
        raw = form.get(name)
        if raw is None:
            return current
        if hasattr(raw, "filename") and hasattr(raw, "file"):
            return current
        value = str(raw).strip()
        return value or None

    mode = _form_text("mode", mode) or "local"
    remote_cag_url = _form_text("remote_cag_url", remote_cag_url)
    remote_image_url = _form_text("remote_image_url", remote_image_url)
    remote_mcp_url = _form_text("remote_mcp_url", remote_mcp_url)
    platform = _form_text("platform", platform)
    model = _form_text("model", model)
    ollama_target = _form_text("ollama_target", ollama_target)
    profile_id = _form_text("profile_id", profile_id) or _form_text("profile", None) or x_profile_id
    resolved_profile = _canonical_profile(_resolved_profile(profile_id))
    _profile_catalog().ensure_profile(resolved_profile, name=resolved_profile)

    suffix = Path(image.filename or "capture.png").suffix or ".png"
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, prefix="copilot_capture_"
        ) as tmp:
            tmp.write(await image.read())
            tmp_path = Path(tmp.name)

        result = run_image_once(
            image_path=tmp_path,
            mode=mode,
            remote_cag_url=remote_cag_url,
            remote_image_url=remote_image_url,
            remote_mcp_url=remote_mcp_url,
            platform=platform,
            model=model,
            ollama_target=ollama_target,
            profile_id=resolved_profile,
        )
        if isinstance(result, dict):
            result["profile_id"] = resolved_profile
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return result


@app.post("/copilot/cag-process", dependencies=[Depends(require_api_key)])
async def copilot_cag_process(
    req: CagProcessRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-ID"),
):
    """
    Direct CAG processing endpoint to avoid relying on LLM tool selection.
    """
    try:
        settings = get_settings()
        settings.require_groups("openai", "supabase")
    except SettingsError as exc:
        raise HTTPException(status_code=500, detail=f"Missing config: {exc}") from exc

    try:
        file_path = (req.path or req.message or "").strip()
        if not file_path:
            raise HTTPException(status_code=400, detail="Missing file path")
        resolved_profile = _canonical_profile(_resolved_profile(req.profile_id or x_profile_id))
        _profile_catalog().ensure_profile(resolved_profile, name=resolved_profile)
        safe_path = _ensure_allowed_file_path(file_path)
        text = extract_text_from_file(str(safe_path))
        result = CAGAgent().process_document_with_cag(
            text, source=str(safe_path), profile_id=resolved_profile
        )
        _profile_catalog().record_artifact(
            profile_id=resolved_profile,
            agent="cag_process",
            artifact_type="cag_ingestion",
            path=str(safe_path),
            run_id=safe_doc_slug(safe_path.stem),
            source_ids=[str(safe_path)],
            metadata=result,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"ok": True, "result": result}


@app.post("/copilot/prepare-markdown", dependencies=[Depends(require_api_key)])
async def copilot_prepare_markdown(
    req: PrepareMarkdownRequest,
    x_profile_id: str | None = Header(default=None, alias="X-Profile-ID"),
):
    try:
        source_path = _ensure_allowed_file_path(req.path.strip())
        if not source_path.exists():
            raise HTTPException(status_code=404, detail="Source file not found")
        resolved_profile = _canonical_profile(_resolved_profile(req.profile_id or x_profile_id))
        _profile_catalog().ensure_profile(resolved_profile, name=resolved_profile)
        run_id = uuid.uuid4().hex[:8]
        target_dir = _resolve_profile_output_dir(
            resolved_profile, "prepared_markdown"
        ) / run_id
        markdown_path = _prepare_markdown_from_path(source_path, target_dir=target_dir)
        _profile_catalog().record_artifact(
            profile_id=resolved_profile,
            agent="prepare_markdown",
            artifact_type="prepared_markdown",
            path=str(markdown_path),
            run_id=run_id,
            source_ids=[str(source_path)],
            metadata={"source_path": str(source_path)},
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "ok": True,
        "source_path": str(source_path),
        "markdown_path": str(markdown_path),
        "profile_id": resolved_profile,
    }


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
