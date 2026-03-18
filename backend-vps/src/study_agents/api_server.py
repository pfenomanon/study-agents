from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import hmac
import os
import re
import secrets
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from aiohttp import web

from .cag_agent import CAGAgent
from .profile_namespace import normalize_profile_id, resolve_profile_id
from .security import (
    RateLimiter,
    SECURITY_HEADERS,
    extract_client_ip,
    extract_auth_token,
    parse_trusted_proxy_networks,
    token_matches,
)
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
API_REQUIRE_TOKEN = os.getenv("API_REQUIRE_TOKEN", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TRUSTED_PROXY_CIDRS = parse_trusted_proxy_networks(os.getenv("TRUSTED_PROXY_CIDRS"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", "10485760"))  # 10MB default
API_RATE_LIMIT_PER_MINUTE = int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "120"))
DELETE_TEMP_IMAGES = os.getenv("DELETE_TEMP_IMAGES", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg"}
ALLOWED_PLATFORMS = {"openai", "ollama"}
ALLOWED_OLLAMA_TARGETS = {"local", "cloud"}
CAPTURE_SESSION_TTL_MINUTES = int(os.getenv("CAPTURE_SESSION_TTL_MINUTES", "120"))
CAPTURE_SESSION_MAX_ENTRIES = int(os.getenv("CAPTURE_SESSION_MAX_ENTRIES", "200"))
CAPTURE_VIEWER_TOKEN_TTL_MINUTES = int(os.getenv("CAPTURE_VIEWER_TOKEN_TTL_MINUTES", "240"))

if API_REQUIRE_TOKEN and not API_TOKEN:
    raise RuntimeError(
        "API token is required but missing. Set API_TOKEN or disable with API_REQUIRE_TOKEN=false."
    )


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


def _parse_profile_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return normalize_profile_id(normalized)
    except ValueError as exc:
        raise ValueError(f"Invalid profile_id: {exc}") from exc


def _resolve_effective_profile_id(explicit_profile_id: Optional[str]) -> Optional[str]:
    # Honor explicit request first; otherwise use active/default profile context
    # only when configured by profile namespace state.
    return resolve_profile_id(explicit_profile_id, allow_default=False)


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


def _request_client_ip(request: web.Request) -> str:
    return extract_client_ip(
        request.remote,
        request.headers.get("X-Forwarded-For"),
        TRUSTED_PROXY_CIDRS,
    )


def _extract_answer_sections(answer_text: str) -> Dict[str, str]:
    sections: Dict[str, str] = {"Answer": "", "Rationale": "", "Citations": ""}
    current: Optional[str] = None
    for raw_line in (answer_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered.startswith("answer:"):
            current = "Answer"
            sections[current] = line.split(":", 1)[1].strip()
            continue
        if lowered.startswith("rationale:"):
            current = "Rationale"
            sections[current] = line.split(":", 1)[1].strip()
            continue
        if lowered.startswith("citations:"):
            current = "Citations"
            sections[current] = line.split(":", 1)[1].strip()
            continue
        if current:
            sections[current] = f"{sections[current]} {line}".strip()
    if not any(sections.values()):
        sections["Answer"] = (answer_text or "").strip()
    if not sections["Rationale"]:
        sections["Rationale"] = "N/A"
    if not sections["Citations"]:
        sections["Citations"] = "NONE"
    return sections


def _capture_session_page_html(session_id: str) -> str:
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Capture Session {session_id}</title>
    <style>
      :root {{
        color-scheme: dark;
        --bg: #071024;
        --card: #0d1b33;
        --line: #1f3558;
        --text: #e5eefc;
        --muted: #9fb3d9;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        background: radial-gradient(circle at top right, #102448, var(--bg));
        color: var(--text);
      }}
      .wrap {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
      h1 {{ margin: 0 0 8px; font-size: 20px; }}
      .meta {{ color: var(--muted); margin-bottom: 16px; }}
      .card {{
        border: 1px solid var(--line);
        border-radius: 12px;
        background: linear-gradient(180deg, #0d1b33, #0a1730);
        padding: 14px;
        margin-bottom: 12px;
      }}
      .row {{ margin: 10px 0; }}
      .label {{ color: #c9dbff; font-weight: 700; margin-bottom: 6px; }}
      .txt {{ white-space: pre-wrap; line-height: 1.45; }}
      .empty {{ border: 1px dashed var(--line); border-radius: 12px; padding: 16px; color: var(--muted); }}
      .gate {{
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.7);
        display: grid;
        place-items: center;
        padding: 16px;
      }}
      .gate-box {{
        width: min(460px, 96vw);
        border: 1px solid var(--line);
        border-radius: 14px;
        background: #0b1831;
        padding: 16px;
      }}
      .gate-box h2 {{ margin: 0 0 8px; font-size: 18px; }}
      .gate-box p {{ margin: 0 0 12px; color: var(--muted); }}
      .gate-box input {{
        width: 100%;
        background: #091427;
        color: var(--text);
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 10px 12px;
        margin-bottom: 12px;
      }}
      .gate-box button {{
        border: 1px solid #3b5ea8;
        border-radius: 10px;
        background: #173469;
        color: #e7f1ff;
        padding: 10px 12px;
        cursor: pointer;
      }}
      .err {{ color: #ff9f9f; min-height: 20px; margin-top: 8px; }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <h1>Remote Vision Capture Session</h1>
      <div class="meta">Session: {session_id}</div>
      <div id="content"></div>
    </div>

    <div class="gate" id="gate">
      <div class="gate-box">
        <h2>Enter Access Code</h2>
        <p>Use the 6-character code shown in the CLI on the initiating machine (spaces/dashes ignored).</p>
        <input id="codeInput" maxlength="12" placeholder="Access code" autocomplete="one-time-code" />
        <button id="unlockBtn">Unlock</button>
        <div class="err" id="err"></div>
      </div>
    </div>

    <script>
      const sid = {session_id!r};
      const gate = document.getElementById("gate");
      const content = document.getElementById("content");
      const codeInput = document.getElementById("codeInput");
      const unlockBtn = document.getElementById("unlockBtn");
      const errEl = document.getElementById("err");
      const tokenKey = "capture_viewer_token_" + sid;
      let viewerToken = sessionStorage.getItem(tokenKey) || "";

      function row(label, value) {{
        const r = document.createElement("div");
        r.className = "row";
        const l = document.createElement("div");
        l.className = "label";
        l.textContent = label;
        const t = document.createElement("div");
        t.className = "txt";
        t.textContent = value || "";
        r.appendChild(l);
        r.appendChild(t);
        return r;
      }}

      function renderEntries(entries) {{
        content.innerHTML = "";
        if (!entries.length) {{
          const empty = document.createElement("div");
          empty.className = "empty";
          empty.textContent = "Waiting for captures...";
          content.appendChild(empty);
          return;
        }}
        for (const e of entries) {{
          const card = document.createElement("div");
          card.className = "card";
          card.appendChild(row("Question:", e.question));
          card.appendChild(row("Answer:", e.answer));
          card.appendChild(row("Rationale:", e.rationale));
          card.appendChild(row("Citations:", e.citations));
          content.appendChild(card);
        }}
      }}

      async function verifyCode() {{
        const code = (codeInput.value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
        if (!code) {{
          errEl.textContent = "Code is required.";
          return;
        }}
        codeInput.value = code;
        errEl.textContent = "";
        const resp = await fetch(`/capture-session/${{sid}}/verify`, {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ code }}),
        }});
        const data = await resp.json().catch(() => ({{}}));
        if (!resp.ok || !data.ok || !data.viewer_token) {{
          errEl.textContent = data.error || "Invalid code.";
          return;
        }}
        viewerToken = data.viewer_token;
        sessionStorage.setItem(tokenKey, viewerToken);
        gate.style.display = "none";
        await refreshEntries();
      }}

      async function refreshEntries() {{
        if (!viewerToken) {{
          gate.style.display = "grid";
          return;
        }}
        const resp = await fetch(`/capture-session/${{sid}}/events`, {{
          headers: {{ "X-Session-Token": viewerToken }},
          cache: "no-store",
        }});
        const data = await resp.json().catch(() => ({{}}));
        if (!resp.ok || !data.ok) {{
          viewerToken = "";
          sessionStorage.removeItem(tokenKey);
          gate.style.display = "grid";
          errEl.textContent = data.error || "Session access expired.";
          return;
        }}
        renderEntries(Array.isArray(data.entries) ? data.entries : []);
      }}

      unlockBtn.addEventListener("click", verifyCode);
      codeInput.addEventListener("keydown", (ev) => {{
        if (ev.key === "Enter") verifyCode();
      }});

      if (viewerToken) {{
        gate.style.display = "none";
        refreshEntries();
      }}
      setInterval(refreshEntries, 2000);
    </script>
  </body>
</html>
"""


class _CaptureSessionStore:
    def __init__(self, default_ttl_minutes: int, max_entries: int, viewer_ttl_minutes: int) -> None:
        self._default_ttl = max(5, default_ttl_minutes)
        self._max_entries = max(10, max_entries)
        self._viewer_ttl = max(5, viewer_ttl_minutes)
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _now() -> _dt.datetime:
        return _dt.datetime.utcnow()

    @staticmethod
    def _generate_code() -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return "".join(secrets.choice(alphabet) for _ in range(6))

    def _purge_expired_locked(self) -> None:
        now = self._now()
        expired_ids = []
        for sid, sess in self._sessions.items():
            expires_at = sess.get("expires_at")
            if isinstance(expires_at, _dt.datetime) and expires_at <= now:
                expired_ids.append(sid)
        for sid in expired_ids:
            self._sessions.pop(sid, None)

    async def create_session(self, ttl_minutes: Optional[int] = None) -> Dict[str, Any]:
        async with self._lock:
            self._purge_expired_locked()
            now = self._now()
            ttl = self._default_ttl if ttl_minutes is None else max(5, min(720, int(ttl_minutes)))
            expires_at = now + _dt.timedelta(minutes=ttl)
            session_id = secrets.token_hex(16)
            code = self._generate_code()
            self._sessions[session_id] = {
                "session_id": session_id,
                "code": code,
                "created_at": now,
                "expires_at": expires_at,
                "entries": [],
                "viewer_token": None,
                "viewer_token_expires_at": None,
            }
            return {
                "session_id": session_id,
                "access_code": code,
                "expires_at": expires_at,
            }

    async def append_entry(
        self,
        session_id: str,
        *,
        question: str,
        answer: str,
        rationale: str,
        citations: str,
    ) -> bool:
        async with self._lock:
            self._purge_expired_locked()
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            entries = sess.setdefault("entries", [])
            entry = {
                "ts_utc": self._now().replace(microsecond=0).isoformat() + "Z",
                "question": (question or "").strip(),
                "answer": (answer or "").strip(),
                "rationale": (rationale or "").strip(),
                "citations": (citations or "").strip(),
            }
            entries.append(entry)
            if len(entries) > self._max_entries:
                sess["entries"] = entries[-self._max_entries :]
            return True

    async def session_exists(self, session_id: str) -> bool:
        async with self._lock:
            self._purge_expired_locked()
            return session_id in self._sessions

    async def verify_code(self, session_id: str, code: str, client_ip: str) -> Dict[str, Any]:
        async with self._lock:
            self._purge_expired_locked()
            sess = self._sessions.get(session_id)
            if not sess:
                return {"ok": False, "error": "Session not found", "status": 404}
            normalized = re.sub(r"[^A-Z0-9]", "", (code or "").strip().upper())
            expected = re.sub(r"[^A-Z0-9]", "", str(sess.get("code", "")).upper())
            if not normalized:
                return {"ok": False, "error": "Missing code", "status": 400}
            if not hmac.compare_digest(normalized, expected):
                return {"ok": False, "error": "Invalid code", "status": 403}
            now = self._now()
            token_expires = min(
                sess["expires_at"],
                now + _dt.timedelta(minutes=self._viewer_ttl),
            )
            viewer_token = secrets.token_urlsafe(24)
            sess["viewer_token"] = viewer_token
            sess["viewer_token_expires_at"] = token_expires
            return {
                "ok": True,
                "viewer_token": viewer_token,
                "expires_at": token_expires.replace(microsecond=0).isoformat() + "Z",
                "status": 200,
            }

    async def get_entries(
        self,
        session_id: str,
        *,
        viewer_token: str,
        client_ip: str,
    ) -> Dict[str, Any]:
        async with self._lock:
            self._purge_expired_locked()
            sess = self._sessions.get(session_id)
            if not sess:
                return {"ok": False, "error": "Session not found", "status": 404}
            expected_token = (sess.get("viewer_token") or "").strip()
            token_expires = sess.get("viewer_token_expires_at")
            if (
                not expected_token
                or not viewer_token
                or not hmac.compare_digest(expected_token, viewer_token.strip())
            ):
                return {"ok": False, "error": "Unauthorized", "status": 403}
            if not isinstance(token_expires, _dt.datetime) or token_expires <= self._now():
                return {"ok": False, "error": "Session access expired", "status": 403}
            entries = list(sess.get("entries", []))
            return {"ok": True, "entries": entries, "status": 200}


async def _run_cag_answer(
    agent: CAGAgent,
    question: str,
    *,
    platform: Optional[str] = None,
    model: Optional[str] = None,
    ollama_target: Optional[str] = None,
    profile_id: Optional[str] = None,
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
            profile_id=profile_id,
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
            "profile_id": profile_id,
        }

    return await loop.run_in_executor(None, _worker)


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD_BYTES + 1024)
    cag = CAGAgent()
    limiter = RateLimiter(max_requests=API_RATE_LIMIT_PER_MINUTE, window_seconds=60)
    capture_sessions = _CaptureSessionStore(
        default_ttl_minutes=CAPTURE_SESSION_TTL_MINUTES,
        max_entries=CAPTURE_SESSION_MAX_ENTRIES,
        viewer_ttl_minutes=CAPTURE_VIEWER_TOKEN_TTL_MINUTES,
    )

    @web.middleware
    async def security_headers_middleware(request: web.Request, handler):
        response = await handler(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @web.middleware
    async def auth_middleware(request: web.Request, handler):
        path = request.path or ""
        allow_without_api_token = False
        if request.method in {"GET", "HEAD"} and re.fullmatch(r"/capture-session/[0-9a-f]{32}/?", path):
            allow_without_api_token = True
        elif request.method == "POST" and re.fullmatch(
            r"/capture-session/[0-9a-f]{32}/verify", path
        ):
            allow_without_api_token = True
        elif request.method in {"GET", "HEAD"} and re.fullmatch(
            r"/capture-session/[0-9a-f]{32}/events", path
        ):
            allow_without_api_token = True
        if allow_without_api_token:
            return await handler(request)
        if API_TOKEN:
            provided = extract_auth_token(request.headers)
            if not provided:
                return web.json_response({"ok": False, "error": "Unauthorized"}, status=401)
            if not token_matches(API_TOKEN, provided):
                return web.json_response({"ok": False, "error": "Forbidden"}, status=403)
        return await handler(request)

    @web.middleware
    async def rate_limit_middleware(request: web.Request, handler):
        client_key = _request_client_ip(request)
        allowed, retry_after = limiter.allow(client_key)
        if not allowed:
            return web.json_response(
                {"ok": False, "error": "Rate limit exceeded"},
                status=429,
                headers={"Retry-After": str(retry_after)},
            )
        return await handler(request)

    app.middlewares.extend([security_headers_middleware, rate_limit_middleware, auth_middleware])

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
            profile_id = _resolve_effective_profile_id(
                _parse_profile_id(data.get("profile_id"))
            )
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        result = await _run_cag_answer(cag, question, profile_id=profile_id, **overrides)
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

        if not request.content_type.lower().startswith("multipart/"):
            return web.json_response(
                {"ok": False, "error": "Content-Type must be multipart/form-data"},
                status=415,
            )

        try:
            reader = await request.multipart()
        except Exception:
            return web.json_response(
                {"ok": False, "error": "Invalid multipart payload"},
                status=400,
            )

        TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        image_id = uuid.uuid4().hex
        img_path = TEMP_IMAGE_DIR / f"capture_{image_id}.png"
        total = 0
        has_image = False
        platform = None
        model = None
        ollama_target = None
        capture_session_id = None
        profile_id = None

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
            if field.name == "capture_session_id":
                capture_session_id = (await field.text()).strip()
                continue
            if field.name == "profile_id":
                profile_id = (await field.text()).strip()
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
            profile_id = _resolve_effective_profile_id(_parse_profile_id(profile_id))
        except ValueError as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

        try:
            q_text = extract_question_with_docling(img_path)
            if not q_text:
                return web.json_response(
                    {
                        "ok": False,
                        "error": "No readable text/question found in screenshot.",
                    },
                    status=400,
                )

            result = await _run_cag_answer(cag, q_text, profile_id=profile_id, **overrides)
            _append_qa_log(
                source="cag-ocr",
                question=result.get("question", q_text),
                answer=result.get("answer", ""),
                context_snippet=result.get("context_snippet", ""),
            )
            if capture_session_id:
                sections = _extract_answer_sections(result.get("answer", ""))
                appended = await capture_sessions.append_entry(
                    capture_session_id,
                    question=result.get("question", q_text),
                    answer=sections.get("Answer", ""),
                    rationale=sections.get("Rationale", ""),
                    citations=sections.get("Citations", ""),
                )
                result["capture_session_id"] = capture_session_id
                result["capture_session_appended"] = bool(appended)
            if not DELETE_TEMP_IMAGES:
                result["screenshot_path"] = str(img_path)
            return web.json_response(result)
        finally:
            if DELETE_TEMP_IMAGES:
                with contextlib.suppress(Exception):
                    img_path.unlink(missing_ok=True)

    async def capture_session_start(request: web.Request) -> web.Response:
        data: Dict[str, Any] = {}
        if request.can_read_body:
            with contextlib.suppress(Exception):
                payload = await request.json()
                if isinstance(payload, dict):
                    data = payload
        ttl_minutes = data.get("ttl_minutes")
        try:
            created = await capture_sessions.create_session(
                ttl_minutes=int(ttl_minutes) if ttl_minutes is not None else None
            )
        except Exception as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)
        session_id = created["session_id"]
        forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        forwarded_host = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
        scheme = forwarded_proto if forwarded_proto in {"http", "https"} else request.scheme
        host = forwarded_host or request.host
        access_url = f"{scheme}://{host}/capture-session/{session_id}"
        return web.json_response(
            {
                "ok": True,
                "session_id": session_id,
                "access_code": created["access_code"],
                "access_url": access_url,
                "expires_at": created["expires_at"].replace(microsecond=0).isoformat() + "Z",
            }
        )

    async def capture_session_page(request: web.Request) -> web.Response:
        session_id = request.match_info.get("session_id", "")
        if not await capture_sessions.session_exists(session_id):
            return web.Response(text="Session not found or expired.", status=404)
        return web.Response(
            text=_capture_session_page_html(session_id),
            content_type="text/html",
        )

    async def capture_session_verify(request: web.Request) -> web.Response:
        session_id = request.match_info.get("session_id", "")
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "Invalid JSON body"}, status=400)
        code = (data.get("code") or "").strip() if isinstance(data, dict) else ""
        client_ip = _request_client_ip(request)
        verified = await capture_sessions.verify_code(session_id, code, client_ip)
        status = int(verified.pop("status", 200))
        return web.json_response(verified, status=status)

    async def capture_session_events(request: web.Request) -> web.Response:
        session_id = request.match_info.get("session_id", "")
        viewer_token = (
            (request.headers.get("X-Session-Token") or "").strip()
            or (request.query.get("viewer_token") or "").strip()
        )
        client_ip = _request_client_ip(request)
        events = await capture_sessions.get_entries(
            session_id,
            viewer_token=viewer_token,
            client_ip=client_ip,
        )
        status = int(events.pop("status", 200))
        return web.json_response(events, status=status)

    app.router.add_post("/capture-session/start", capture_session_start)
    app.router.add_get("/capture-session/{session_id:[0-9a-f]{32}}", capture_session_page)
    app.router.add_post("/capture-session/{session_id:[0-9a-f]{32}}/verify", capture_session_verify)
    app.router.add_get("/capture-session/{session_id:[0-9a-f]{32}}/events", capture_session_events)
    app.router.add_post("/cag-answer", cag_answer)
    app.router.add_post("/cag-ocr-answer", cag_ocr_answer)
    return app


def main() -> None:
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
