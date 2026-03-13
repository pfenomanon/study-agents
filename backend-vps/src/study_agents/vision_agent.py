# src/study_agents/vision_agent.py

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    REASON_MODEL,
    TARGET_MONITOR,
    CAPTURE_INTERVAL,
    OPENAI_API_KEY,
    OPENAI_EMBED_MODEL,
    OLLAMA_HOST,
    OLLAMA_API_KEY,
    SCREENSHOT_DIR,
)
from .prompt_loader import load_prompt

# --- ENV for Ollama Cloud/local ---
if OLLAMA_HOST:
    os.environ["OLLAMA_HOST"] = OLLAMA_HOST
if OLLAMA_API_KEY:
    os.environ["OLLAMA_API_KEY"] = OLLAMA_API_KEY

try:  # optional vision extras
    import keyboard  # type: ignore
except ImportError:
    keyboard = None

try:
    import mss  # type: ignore
    import mss.tools  # type: ignore
except ImportError:
    mss = None
    mss_tools = None
else:
    mss_tools = mss.tools

try:
    from docling.document_converter import DocumentConverter
except Exception as e:  # pragma: no cover - optional dependency
    print(f"⚠️ Docling unavailable, will use RapidOCR only: {e}")
    DocumentConverter = None
from openai import OpenAI

from .ollama_client import chat as ollama_chat
from .cag_agent import CAGAgent
from .supabase_client import get_supabase_client

import requests
# --- CLIENTS (lazy to avoid import-time crashes) ---
_supabase_client = None
_openai_client = None
docling = DocumentConverter() if DocumentConverter is not None else None
local_cag_agent: CAGAgent | None = None


def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = get_supabase_client()
    return _supabase_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client

# ---------------- REASONING ROLE ----------------
_DEFAULT_VISION_PROMPT = """You are a subject-matter-expert assistant.
Prioritize the retrieved CONTEXT when answering the question extracted from the screenshot.
If the context is thin or missing, you may rely on broader professional expertise and explicitly note that it comes from prior knowledge.
Provide reasoning concisely with practical relevance.

Format:
Answer: <concise answer>
Rationale: <brief justification>
Citations: <qa_id(s) or 'Professional knowledge'>
"""

REASONING_SYSTEM = load_prompt("vision_reasoning.txt", _DEFAULT_VISION_PROMPT)


# ---------------- SCREEN CAPTURE ----------------
def capture_monitor(
    monitor_index: int = 1,
    top_offset: int | None = None,
    bottom_offset: int | None = None,
    left_offset: int | None = None,
    right_offset: int | None = None,
    region: dict[str, int] | None = None,
) -> Path:
    """
    Capture a screenshot of the configured monitor (with a small top offset)
    and save it into SCREENSHOT_DIR.
    """
    if mss is None or mss_tools is None:
        raise RuntimeError(
            "Screen capture requires the optional 'vision' dependencies. "
            "Install with `pip install study-agents[vision]`."
        )

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        monitor = sct.monitors[monitor_index]

        if region:
            region = {
                "top": region.get("top", monitor["top"]),
                "left": region.get("left", monitor["left"]),
                "width": region.get("width", monitor["width"]),
                "height": region.get("height", monitor["height"]),
            }
        else:
            top_offset = 100 if top_offset is None else top_offset
            bottom_offset = 0 if bottom_offset is None else bottom_offset
            left_offset = 0 if left_offset is None else left_offset
            right_offset = 0 if right_offset is None else right_offset

            region = {
                "top": monitor["top"] + top_offset,
                "left": monitor["left"] + left_offset,
                "width": monitor["width"] - right_offset,
                "height": monitor["height"] - top_offset - bottom_offset,
            }

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"docling_capture_{ts}.png"
        sct_img = sct.grab(region)
        mss_tools.to_png(sct_img.rgb, sct_img.size, output=str(path))
        return path


# ---------------- OCR + QUESTION EXTRACTION ----------------
def extract_question_with_docling(image_path: Path) -> str:
    """
    Extracts text from a screenshot using Docling OCR and falls back to RapidOCR if Docling returns nothing.
    Compatible with older Docling versions (no fmt argument).
    """
    text = ""
    if docling is not None:
        try:
            result = docling.convert(str(image_path))  # NOTE: no fmt argument

            # Try all known Docling structures for text extraction
            if hasattr(result, "document"):
                doc = result.document
                # Newer Docling versions expose OCR text via export APIs.
                if hasattr(doc, "export_to_markdown"):
                    md = doc.export_to_markdown()
                    if md and md.strip() and md.strip() != "<!-- image -->":
                        text = md.replace("<!-- image -->", "").strip()
                if not text and hasattr(doc, "export_to_text"):
                    txt = doc.export_to_text()
                    if txt and txt.strip():
                        text = txt.strip()
                if not text and hasattr(doc, "text_content") and doc.text_content:
                    text = doc.text_content
                elif not text and hasattr(doc, "text") and doc.text:
                    text = doc.text
                elif not text and hasattr(doc, "pages"):
                    for page in doc.pages:
                        if hasattr(page, "text") and page.text:
                            text += page.text + "\n"
                        elif hasattr(page, "elements"):
                            for el in page.elements:
                                if hasattr(el, "text") and el.text:
                                    text += el.text + "\n"

            elif hasattr(result, "documents"):
                for doc in result.documents:
                    if hasattr(doc, "text") and doc.text:
                        text += doc.text + "\n"
                    elif hasattr(doc, "pages"):
                        for page in doc.pages:
                            if hasattr(page, "text") and page.text:
                                text += page.text + "\n"

            text = text.strip()

        except Exception as e:
            print(f"⚠️ Docling extraction error: {e}")
    else:
        print("⚠️ Docling not available, skipping to RapidOCR fallback.")

    # Fallback RapidOCR
    if not text:
        print("⚠️ Docling returned no text. Trying RapidOCR fallback.")
        try:
            from rapidocr_onnxruntime import RapidOCR

            ocr = RapidOCR()
            result, _ = ocr(str(image_path))
            if result:
                text = "\n".join([t[1] for t in result])
        except Exception as e:
            print(f"⚠️ RapidOCR fallback failed: {e}")

    # Final fallback: pytesseract (useful when RapidOCR runtime/model download fails on servers)
    if not text:
        print("⚠️ RapidOCR produced no text. Trying pytesseract fallback.")
        try:
            import pytesseract
            from PIL import Image

            text = pytesseract.image_to_string(Image.open(str(image_path)))
        except Exception as e:
            print(f"⚠️ pytesseract fallback failed: {e}")
            return ""

    if not text.strip():
        print("⚠️ OCR produced no usable text.")
        return ""

    print("\n🧾 [OCR Raw Output]\n", text[:1000], "\n")

    # Parse question and options
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    q_lines, options = [], []
    for line in lines:
        lower = line.lower()
        if lower.startswith(("a)", "b)", "c)", "d)", "1.", "2.", "3.")):
            options.append(line)
        else:
            q_lines.append(line)

    question_text = " ".join(q_lines)
    if options:
        question_text += "\nOptions:\n" + "\n".join(options)

    return question_text.strip()


# ---------------- EMBEDDING & RETRIEVAL ----------------
def embed_text(text: str) -> list[float]:
    emb = _get_openai().embeddings.create(model=OPENAI_EMBED_MODEL, input=text)
    return emb.data[0].embedding


def retrieve_context(question: str, threshold: float = 0.1, k: int = 10):
    try:
        embedding = embed_text(question)
        res = _get_supabase().rpc(
            "match_documents",
            {
                "query_embedding": embedding,
                "match_threshold": threshold,
                "match_count": k,
            },
        ).execute()
    except Exception as exc:
        print(f"?? Context retrieval failed: {exc}")
        return "", []

    if not res.data:
        return "", []

    rows = res.data
    pairs, ctx_parts = [], []
    for r in rows:
        sim = r.get("similarity") or r.get("score") or 0.0
        content = r.get("content") or ""
        if content:
            pairs.append((r.get("id", "?"), float(sim)))
            ctx_parts.append(content)

    context = "\n\n---\n\n".join(ctx_parts)
    return context, pairs


# ---------------- REASONING ----------------
def reason_answer(question: str, context: str, used_ids=None) -> str:
    if not context.strip():
        context = "(Context unavailable; rely on professional knowledge.)"
    used_ids = used_ids or []
    user_prompt = (
        f"QUESTION:\n{question}\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"RETRIEVED_IDS: {', '.join(used_ids)}"
    )
    result = ollama_chat(
        model=REASON_MODEL,
        messages=[
            {"role": "system", "content": REASONING_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
    )
    try:
        # Newer ollama Python client returns .message.content; older might be dict
        if isinstance(result, dict):
            return result["message"]["content"].strip()
        return result.message["content"].strip()
    except Exception:
        return "⚠️ No valid output from reasoning model."


def _call_remote_inspect_graph(question: str, mcp_host: str) -> str:
    mcp_cli = shutil.which("mcp-cli")
    if not mcp_cli:
        raise RuntimeError("`mcp-cli` is required for remote MCP calls but not found.")

    payload = json.dumps({"question": question})
    cmd = [mcp_cli, "call", "study-agents-fixed", "inspect_graph", payload]
    if mcp_host:
        cmd += ["--host", mcp_host]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Remote MCP call failed ({result.returncode}): {result.stderr.strip()}"
        )

    try:
        data = json.loads(result.stdout)
        return data.get("output", result.stdout).strip()
    except json.JSONDecodeError:
        return result.stdout.strip()


# ---------------- ONE-SHOT ENTRYPOINT (for MCP) ----------------
def _build_runtime_payload(
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
) -> dict:
    payload: dict[str, str] = {}
    if platform:
        payload["platform"] = platform.strip()
    if model:
        payload["model"] = model.strip()
    if ollama_target:
        payload["ollama_target"] = ollama_target.strip()
    return payload


def _call_remote_cag(
    question: str,
    url: str,
    *,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
) -> dict:
    payload = {"question": question}
    payload.update(_build_runtime_payload(platform, model, ollama_target))
    headers = {}
    remote_api_token = (os.getenv("REMOTE_API_TOKEN") or "").strip()
    if remote_api_token:
        headers["X-API-Key"] = remote_api_token
    resp = requests.post(url, json=payload, headers=headers or None, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data


def run_capture_once(
    mode: str = "local",
    remote_cag_url: str | None = None,
    remote_mcp_url: str | None = None,
    remote_image_url: str | None = None,
    monitor_index: int | None = None,
    top_offset: int | None = None,
    bottom_offset: int | None = None,
    left_offset: int | None = None,
    right_offset: int | None = None,
    region: dict[str, int] | None = None,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
) -> dict:
    """
    One-shot capture + OCR + retrieval + reasoning.

    Returns:
      {
        "ok": bool,
        "question": str,
        "answer": str,
        "context_ids": list[str],
        "context_snippet": str,
        "screenshot_path": str
      }
    """
    img_path = capture_monitor(
        monitor_index or TARGET_MONITOR,
        top_offset=top_offset,
        bottom_offset=bottom_offset,
        left_offset=left_offset,
        right_offset=right_offset,
        region=region,
    )

    q_text = extract_question_with_docling(img_path)
    if not q_text:
        return {
            "ok": False,
            "question": "",
            "answer": "⚠️ No readable text/question found in screenshot.",
            "context_ids": [],
            "context_snippet": "",
            "screenshot_path": str(img_path),
        }

    mode_normalized = (mode or "local").lower()

    if mode_normalized == "remote_image" and remote_image_url:
        try:
            runtime_payload = _build_runtime_payload(platform, model, ollama_target)
            with open(img_path, "rb") as f:
                headers = {}
                remote_api_token = (os.getenv("REMOTE_API_TOKEN") or "").strip()
                if remote_api_token:
                    headers["X-API-Key"] = remote_api_token
                resp = requests.post(
                    remote_image_url,
                    files={"image": f},
                    data=runtime_payload or None,
                    headers=headers or None,
                    timeout=120,
                )
            resp.raise_for_status()
            data = resp.json()
            return {
                "ok": bool(data.get("ok", True)),
                "question": data.get("question", q_text),
                "answer": data.get("answer", ""),
                "context_ids": [],
                "context_snippet": data.get("context_snippet", ""),
                "screenshot_path": str(img_path),
                "remote_result": data,
            }
        except Exception as exc:
            return {
                "ok": False,
                "question": q_text,
                "answer": f"Remote image CAG error: {exc}",
                "context_ids": [],
                "context_snippet": "",
                "screenshot_path": str(img_path),
                "remote_result": None,
            }

    if mode_normalized == "remote" and remote_cag_url:
        try:
            remote = _call_remote_cag(
                q_text,
                remote_cag_url,
                platform=platform,
                model=model,
                ollama_target=ollama_target,
            )
            return {
                "ok": bool(remote.get("ok", True)),
                "question": remote.get("question", q_text),
                "answer": remote.get("answer", ""),
                "context_ids": [],
                "context_snippet": remote.get("context_snippet", ""),
                "screenshot_path": str(img_path),
                "remote_result": remote,
            }
        except Exception as exc:
            return {
                "ok": False,
                "question": q_text,
                "answer": f"Remote CAG error: {exc}",
                "context_ids": [],
                "context_snippet": "",
                "screenshot_path": str(img_path),
                "remote_result": None,
            }

    # Local answering path: default is enhanced CAG retrieval.
    global local_cag_agent
    if local_cag_agent is None:
        local_cag_agent = CAGAgent()
    context = local_cag_agent.enhanced_retrieve_context(q_text, top_k=12)
    used_ids: list[str] = []
    snippet = context[:500] + ("…" if len(context) > 500 else "")
    ans_block = reason_answer(q_text, context, used_ids)

    remote_result = None
    if remote_mcp_url:
        try:
            remote_result = _call_remote_inspect_graph(q_text, remote_mcp_url)
        except Exception as exc:
            remote_result = f"Remote MCP call error: {exc}"

    return {
        "ok": True,
        "question": q_text,
        "answer": ans_block,
        "context_ids": used_ids,
        "context_snippet": snippet,
        "screenshot_path": str(img_path),
        "remote_result": remote_result,
    }


# ---------------- INTERACTIVE LOOP (CLI use) ----------------
REMOTE_MCP_URL = os.getenv("REMOTE_MCP_URL")
REMOTE_MODE = (os.getenv("REMOTE_MODE") or "local").lower()
REMOTE_CAG_URL = os.getenv("REMOTE_CAG_URL")
REMOTE_IMAGE_URL = os.getenv("REMOTE_IMAGE_URL")


def main_loop(
    mode: str | None = None,
    remote_cag_url: str | None = None,
    remote_mcp_url: str | None = REMOTE_MCP_URL,
    top_offset: int | None = None,
    bottom_offset: int | None = None,
    left_offset: int | None = None,
    right_offset: int | None = None,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
):
    effective_mode = (mode or REMOTE_MODE or "local").lower()
    effective_remote_cag = remote_cag_url or REMOTE_CAG_URL
    effective_remote_image = REMOTE_IMAGE_URL

    if keyboard is None:
        raise RuntimeError(
            "Interactive capture loop requires the 'keyboard' package. "
            "Install optional extras with `pip install study-agents[vision]`."
        )

    print("📄 Docling Capture Agent active...")
    print("Press 'Z' to capture and process current monitor. Press 'Esc' to exit.")
    print(f"Mode: {effective_mode}")

    seen = set()

    while True:
        if keyboard.is_pressed("esc"):
            print("\n🛑 Docling agent stopped.")
            break

        if keyboard.is_pressed("z"):
            time.sleep(0.15)
            print("\n📸 Capturing screenshot...")
            img_path = capture_monitor(
                TARGET_MONITOR,
                top_offset=top_offset,
                bottom_offset=bottom_offset,
                left_offset=left_offset,
                right_offset=right_offset,
            )

            if effective_mode == "remote_image" and effective_remote_image:
                try:
                    runtime_payload = _build_runtime_payload(platform, model, ollama_target)
                    with open(img_path, "rb") as f:
                        headers = {}
                        remote_api_token = (os.getenv("REMOTE_API_TOKEN") or "").strip()
                        if remote_api_token:
                            headers["X-API-Key"] = remote_api_token
                        resp = requests.post(
                            effective_remote_image,
                            files={"image": f},
                            data=runtime_payload or None,
                            headers=headers or None,
                            timeout=120,
                        )
                    resp.raise_for_status()
                    data = resp.json()
                    remote_question = (data.get("question") or "").strip()
                    if remote_question:
                        preview = remote_question[:700] + ("..." if len(remote_question) > 700 else "")
                        print(f"❓ Remote OCR extracted:\n{preview}\n")
                    if data.get("context_length") is not None:
                        print(f"📚 Remote context length: {data.get('context_length')}")
                    remote_snippet = (data.get("context_snippet") or "").strip()
                    if remote_snippet:
                        print(f"🧩 Remote context sample:\n{remote_snippet[:500]}\n")
                    print(f"💡 {data.get('answer', '')}\n")
                except Exception as exc:
                    print(f"⚠️ Remote image CAG call failed: {exc}\n")
                time.sleep(0.6)
                continue

            q_text = extract_question_with_docling(img_path)
            if not q_text:
                print("⚠️ No readable text/question found in screenshot.")
                time.sleep(0.5)
                continue

            if q_text in seen:
                print("↩️ Duplicate question detected; skipping.")
                time.sleep(0.5)
                continue
            seen.add(q_text)

            print(f"\n❓ Extracted:\n{q_text}\n")

            if effective_mode == "remote" and effective_remote_cag:
                try:
                    remote = _call_remote_cag(
                        q_text,
                        effective_remote_cag,
                        platform=platform,
                        model=model,
                        ollama_target=ollama_target,
                    )
                    print(f"💡 {remote.get('answer', '')}\n")
                except Exception as exc:
                    print(f"⚠️ Remote CAG call failed: {exc}\n")
            else:
                # Default local path: use enhanced CAG retrieval (vector + KG)
                global local_cag_agent
                if local_cag_agent is None:
                    local_cag_agent = CAGAgent()
                context = local_cag_agent.enhanced_retrieve_context(q_text, top_k=12)
                used_ids: list[str] = []

                if context:
                    snippet = context[:500] + ("…" if len(context) > 500 else "")
                    print("\n🧩 Enhanced context sample:\n", snippet, "\n")
                else:
                    print("→ No context from enhanced CAG retrieval.")

                ans_block = reason_answer(q_text, context, used_ids)
                print(f"💡 {ans_block}\n")

                if remote_mcp_url:
                    try:
                        remote_answer = _call_remote_inspect_graph(q_text, remote_mcp_url)
                        print(f"🌐 Remote MCP result:\n{remote_answer}\n")
                    except Exception as exc:
                        print(f"⚠️ Remote MCP call failed: {exc}\n")

            time.sleep(0.6)
        time.sleep(0.05)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Docling/vision capture agent")
    parser.add_argument(
        "--mode",
        choices=["local", "remote", "remote_image"],
        default=REMOTE_MODE,
        help="Execution mode: local (default), remote (text to CAG API), or remote_image (image to CAG OCR API).",
    )
    parser.add_argument(
        "--remote-cag-url",
        default=REMOTE_CAG_URL,
        help="Override REMOTE_CAG_URL (text question endpoint).",
    )
    parser.add_argument(
        "--remote-image-url",
        default=REMOTE_IMAGE_URL,
        help="Override REMOTE_IMAGE_URL (image OCR endpoint).",
    )
    parser.add_argument(
        "--remote-mcp-url",
        default=REMOTE_MCP_URL,
        help="Optional remote MCP URL for inspect_graph.",
    )
    parser.add_argument(
        "--platform",
        choices=["openai", "ollama"],
        default=None,
        help="Reasoning platform override sent to remote backend.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Reasoning model override sent to remote backend.",
    )
    parser.add_argument(
        "--ollama-target",
        choices=["local", "cloud"],
        default=None,
        help="When platform=ollama, choose local or cloud routing on remote backend.",
    )
    parser.add_argument(
        "--dpi",
        type=float,
        default=96.0,
        help="Monitor DPI used to convert inch margins to pixels (default: 96).",
    )
    parser.add_argument("--top-in", type=float, default=None, help="Top margin in inches.")
    parser.add_argument("--bottom-in", type=float, default=None, help="Bottom margin in inches.")
    parser.add_argument("--left-in", type=float, default=None, help="Left margin in inches.")
    parser.add_argument("--right-in", type=float, default=None, help="Right margin in inches.")

    args = parser.parse_args()

    def _to_px(v: float | None) -> int | None:
        return int(v * args.dpi) if v is not None else None

    top_px = _to_px(args.top_in)
    bottom_px = _to_px(args.bottom_in)
    left_px = _to_px(args.left_in)
    right_px = _to_px(args.right_in)

    # Update globals for image mode URL override
    if args.remote_image_url:
        REMOTE_IMAGE_URL = args.remote_image_url

    main_loop(
        mode=args.mode,
        remote_cag_url=args.remote_cag_url,
        remote_mcp_url=args.remote_mcp_url,
        top_offset=top_px,
        bottom_offset=bottom_px,
        left_offset=left_px,
        right_offset=right_px,
        platform=args.platform,
        model=args.model,
        ollama_target=args.ollama_target,
    )
