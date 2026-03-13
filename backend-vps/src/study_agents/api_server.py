from __future__ import annotations

import asyncio
import datetime as _dt
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from .cag_agent import CAGAgent
from .settings import SettingsError, get_settings
from .vision_agent import extract_question_with_docling

_settings = get_settings()
try:
    _settings.require_groups("supabase", "openai")
except SettingsError as exc:
    raise RuntimeError(
        "API server requires Supabase and OpenAI credentials. "
        "Run `study-agents-validate --groups supabase,openai`."
    ) from exc


QA_LOG_DIR = Path(os.getenv("QA_LOG_DIR", "/app/data/qa_sessions"))
QA_LOG_PATH = QA_LOG_DIR / "qa_log.md"
TEMP_IMAGE_DIR = Path(os.getenv("TEMP_IMAGE_DIR", "/app/temp_images"))
API_TOKEN = os.getenv("API_TOKEN")
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))  # 10MB default
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg"}
ALLOWED_PLATFORMS = {"openai", "ollama"}
ALLOWED_OLLAMA_TARGETS = {"local", "cloud"}


def _parse_runtime_overrides(
    *,
    platform: Optional[str] = None,
    model: Optional[str] = None,
    ollama_target: Optional[str] = None,
) -> Dict[str, str]:
    overrides: Dict[str, str] = {}

    if platform is not None:
        normalized = platform.strip().lower()
        if not normalized:
            raise ValueError("platform cannot be empty when provided.")
        if normalized not in ALLOWED_PLATFORMS:
            raise ValueError("platform must be 'openai' or 'ollama'.")
        overrides["platform"] = normalized

    if model is not None:
        normalized = model.strip()
        if not normalized:
            raise ValueError("model cannot be empty when provided.")
        overrides["model"] = normalized

    if ollama_target is not None:
        normalized = ollama_target.strip().lower()
        if not normalized:
            raise ValueError("ollama_target cannot be empty when provided.")
        if normalized not in ALLOWED_OLLAMA_TARGETS:
            raise ValueError("ollama_target must be 'local' or 'cloud'.")
        overrides["ollama_target"] = normalized

    return overrides


def _append_qa_log(source: str, question: str, answer: str, context_snippet: str) -> None:
    QA_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.utcnow().isoformat() + "Z"
    lines = [
        f"## Q-Session {ts}",
        f"Source: {source}",
        "",
        "Question:",
        f"> {question}",
        "",
        "Model Answer:",
        f"> {answer}",
        "",
        "Context Snippet:",
        "```",
        context_snippet or "",
        "```",
        "",
        "User Correction: _pending_",
        "Status: accepted",
        "---",
        "",
    ]
    with QA_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines))


async def _run_cag_answer(
    agent: CAGAgent,
    question: str,
    *,
    platform: Optional[str] = None,
    model: Optional[str] = None,
    ollama_target: Optional[str] = None,
) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()

    def _worker() -> Dict[str, Any]:
        runtime = agent.resolve_reasoning_runtime(
            platform=platform,
            model=model,
            ollama_target=ollama_target,
        )
        context, answer = agent.answer_with_enhanced_cag(
            question,
            platform=runtime.get("platform"),
            model=runtime.get("model"),
            ollama_target=runtime.get("ollama_target"),
        )
        snippet = context[:500] + ("…" if len(context) > 500 else "")
        return {
            "ok": True,
            "question": question,
            "answer": answer,
            "context_snippet": snippet,
            "context_length": len(context),
            "reasoning_platform": runtime.get("platform"),
            "reasoning_model": runtime.get("model"),
            "ollama_target": runtime.get("ollama_target"),
        }

    return await loop.run_in_executor(None, _worker)


def create_app() -> web.Application:
    app = web.Application()
    cag = CAGAgent()

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        if API_TOKEN:
            provided = request.headers.get("X-API-Key") or request.headers.get("Authorization")
            # Accept "Bearer <token>" or raw token
            if not provided:
                return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
            token = provided.replace("Bearer", "").strip()
            if token != API_TOKEN:
                return web.json_response({"ok": False, "error": "Forbidden"}, status=403)
        return await handler(request)

    app.middlewares.append(auth_middleware)

    async def cag_answer(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "Invalid JSON body"}, status=400
            )

        question = (data.get("question") or "").strip()
        if not question:
            return web.json_response(
                {"ok": False, "error": "Missing 'question' field"}, status=400
            )

        try:
            overrides = _parse_runtime_overrides(
                platform=data.get("platform"),
                model=data.get("model"),
                ollama_target=data.get("ollama_target"),
            )
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        result = await _run_cag_answer(cag, question, **overrides)
        _append_qa_log(
            source="cag-text",
            question=result.get("question", question),
            answer=result.get("answer", ""),
            context_snippet=result.get("context_snippet", ""),
        )
        return web.json_response(result)

    async def cag_ocr_answer(request: web.Request) -> web.Response:
        if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
            return web.json_response(
                {"ok": False, "error": "File too large"}, status=413
            )

        reader = await request.multipart()
        TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        image_id = uuid.uuid4().hex
        img_path = TEMP_IMAGE_DIR / f"capture_{image_id}.png"
        total = 0
        has_image = False
        platform = None
        model = None
        ollama_target = None

        while True:
            field = await reader.next()
            if field is None:
                break

            if field.name == "platform":
                platform = (await field.text()).strip()
                continue
            if field.name == "model":
                model = (await field.text()).strip()
                continue
            if field.name == "ollama_target":
                ollama_target = (await field.text()).strip()
                continue
            if field.name != "image":
                try:
                    await field.release()
                except Exception:
                    pass
                continue
            if has_image:
                try:
                    await field.release()
                except Exception:
                    pass
                continue

            if field.headers:
                ctype = field.headers.get("Content-Type")
                if ctype and ctype.lower() not in ALLOWED_IMAGE_TYPES:
                    return web.json_response(
                        {"ok": False, "error": "Unsupported image type"}, status=415
                    )

            has_image = True
            with img_path.open("wb") as f:
                while True:
                    chunk = await field.read_chunk()
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        return web.json_response(
                            {"ok": False, "error": "File too large"}, status=413
                        )
                    f.write(chunk)

        if not has_image:
            return web.json_response(
                {"ok": False, "error": "Expected 'image' field"}, status=400
            )

        try:
            overrides = _parse_runtime_overrides(
                platform=platform,
                model=model,
                ollama_target=ollama_target,
            )
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        q_text = extract_question_with_docling(img_path)
        if not q_text:
            return web.json_response(
                {
                    "ok": False,
                    "error": "No readable text/question found in screenshot.",
                },
                status=400,
            )

        result = await _run_cag_answer(cag, q_text, **overrides)
        _append_qa_log(
            source="cag-ocr",
            question=result.get("question", q_text),
            answer=result.get("answer", ""),
            context_snippet=result.get("context_snippet", ""),
        )
        result["screenshot_path"] = str(img_path)
        return web.json_response(result)

    app.router.add_post("/cag-answer", cag_answer)
    app.router.add_post("/cag-ocr-answer", cag_ocr_answer)
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
