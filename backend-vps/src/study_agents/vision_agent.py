# src/study_agents/vision_agent.py

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

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
from .prompt_loader import load_required_prompt

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

from .cag_agent import CAGAgent
from .security import validate_outbound_url
from .supabase_client import get_supabase_client

import requests
# --- CLIENTS (lazy to avoid import-time crashes) ---
_supabase_client = None
_openai_client = None
docling = DocumentConverter() if DocumentConverter is not None else None
local_cag_agent: CAGAgent | None = None
VISION_ALLOW_PRIVATE_REMOTE_URLS = (
    os.getenv("VISION_ALLOW_PRIVATE_REMOTE_URLS", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)


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
REASONING_SYSTEM = load_required_prompt("vision_reasoning.txt")


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
def _normalize_ocr_text(text: str) -> str:
    """Best-effort cleanup for OCR output with collapsed whitespace."""
    normalized = text.replace("\u00a0", " ").replace("\r\n", "\n").replace("\r", "\n")
    cleaned_lines: list[str] = []
    for raw in normalized.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = re.sub(r"\s+", " ", line)
        # If OCR collapsed most spaces, recover likely boundaries.
        space_ratio = line.count(" ") / max(len(line), 1)
        if len(line) >= 12 and space_ratio < 0.03:
            line = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", line)
            line = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", line)
            line = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", line)
            line = re.sub(r"(?<=[\)\]\}:;,\.\?!])(?=[A-Za-z0-9])", " ", line)
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def _space_ratio(text: str) -> float:
    compact = text.strip()
    if not compact:
        return 0.0
    return compact.count(" ") / max(len(compact), 1)


def _looks_collapsed_text(text: str) -> bool:
    compact = text.strip()
    return len(compact) >= 60 and _space_ratio(compact) < 0.04


def _run_pytesseract_ocr(image_path: Path) -> str:
    try:
        import pytesseract
        from PIL import Image

        return (pytesseract.image_to_string(Image.open(str(image_path))) or "").strip()
    except Exception as exc:
        print(f"⚠️ pytesseract fallback failed: {exc}")
        return ""


def _clean_extracted_question_text(question_text: str) -> str:
    """Remove common UI/metadata noise from OCR question text."""
    if not question_text:
        return ""

    noise_markers = (
        "expert insurance adjuster console",
        "submit structured scenarios",
        "workflow-aligned questions",
        "grounded answers",
    )
    progress_only_pattern = (
        r"(?i)^\s*(?:[<>\[\]\(\){}«»‹›←→\-–—]+\s*)*"
        r"(?:tomorrow\s+)?[qgo0]uestion\s+\d+\s*(?:of|/)\s*\d+\b"
        r"(?:\s*[<>\[\]\(\){}«»‹›←→\-–—]+)*\s*$"
    )
    progress_prefix_pattern = (
        r"(?i)^\s*(?:[<>\[\]\(\){}«»‹›←→\-–—]+\s*)*"
        r"(?:tomorrow\s+)?[qgo0]uestion\s+\d+\s*(?:of|/)\s*\d+\b"
        r"\s*[:\-–—]?\s*"
    )
    progress_pattern = r"(?i)\s*(?:tomorrow\s+)?[qgo0]uestion\s+\d+\s*(?:of|/)\s*\d+\b.*$"
    qsearch_pattern = r"(?i)\bq\s*search\b.*$"

    def _is_noise_line(line: str) -> bool:
        compact = (line or "").strip()
        if not compact:
            return True
        if re.fullmatch(r"(?i)[\-–—]?\s*\d{1,2}:\d{2}\s*(?:AM|PM)?", compact):
            return True
        if re.fullmatch(r"(?i)[\-–—]?\s*\d{1,2}\s*(?:AM|PM)", compact):
            return True
        if re.fullmatch(r"(?i)[\-–—]?\s*of\s+\d{1,4}\b", compact):
            return True
        if re.fullmatch(progress_only_pattern, compact):
            return True
        if re.search(r"(?i)\bsubmit\s*answer\b", compact):
            return True
        if re.search(r"(?i)\b(?:tempstorise|temporise)\b", compact):
            return True
        if re.search(r"(?i)\b\d+\s*x\s*\d+\b", compact):
            return True
        if re.search(r"(?i)\b\d+(?:\.\d+)?\s*(?:kb|mb|gb)\b", compact):
            return True
        if re.fullmatch(r"(?i)[\-–—]?\s*\d{1,2}/\d{1,2}/\d{2,4}\s*", compact):
            return True
        if re.fullmatch(r"(?i)[\-–—]?\s*\d{1,3}\s*°\s*[fc](?:\s+\w+)?", compact):
            return True
        return False

    cleaned_lines: list[str] = []
    for raw_line in question_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Remove progress/navigation fragments early so later prefix-stripping
        # does not leave stem artifacts like "of 150".
        if re.fullmatch(progress_only_pattern, line):
            continue
        line = re.sub(progress_prefix_pattern, "", line)

        # Remove markdown heading markers and optional "Question:" prefixes.
        line = re.sub(r"^\s*#+\s*", "", line)
        line = re.sub(r"(?i)^\s*question\s*:\s*", "", line)
        line = re.sub(r"(?i)^\s*question\s+\d+\s*(?:[\)\.\-:]?\s*)", "", line)
        line = re.sub(r"^\s*\d{2,3}\s*[\)\.\-:]\s*", "", line)
        line = re.sub(r"(?i)\bq\s*search\b", "", line)
        line = re.sub(r"(?i)\bsubmit\s*answer\b", "", line)
        line = re.sub(r"(?i)^\s*\d{1,3}\s*°\s*[fc]\b", "", line)
        line = re.sub(
            r"(?i)^\s*(?:sunny|cloudy|rainy|stormy|windy|snowy|clear|overcast)\b",
            "",
            line,
        )

        # Remove UI navigation/status fragments (e.g., "Question 93 of 150", "QSearch").
        line = re.sub(progress_pattern, "", line)
        line = re.sub(qsearch_pattern, "", line)

        line = re.sub(r"\s+", " ", line).strip()
        if not line or _is_noise_line(line):
            continue

        lowered = line.lower()
        if any(marker in lowered for marker in noise_markers):
            if "?" not in line:
                continue
            for marker in noise_markers:
                line = re.sub(re.escape(marker), "", line, flags=re.IGNORECASE)
            line = re.sub(r"\s+", " ", line).strip(" -|:\n\t")
            if not line:
                continue

        cleaned_lines.append(line)

    if not cleaned_lines:
        fallback = re.sub(r"^\s*#+\s*", "", question_text.strip())
        fallback = re.sub(progress_prefix_pattern, "", fallback)
        fallback = re.sub(r"(?i)^\s*question\s*:\s*", "", fallback)
        fallback = re.sub(r"(?i)^\s*question\s+\d+\s*(?:[\)\.\-:]?\s*)", "", fallback)
        fallback = re.sub(r"(?i)\bq\s*search\b", "", fallback)
        fallback = re.sub(r"(?i)\bsubmit\s*answer\b", "", fallback)
        fallback = re.sub(progress_pattern, "", fallback)
        fallback = re.sub(qsearch_pattern, "", fallback)
        fallback = re.sub(r"(?i)^\s*of\s+\d{1,4}\b\s*", "", fallback)
        for marker in noise_markers:
            fallback = re.sub(re.escape(marker), "", fallback, flags=re.IGNORECASE)
        fallback = re.sub(r"\s+", " ", fallback).strip(" -|:\n\t")
        if "?" not in fallback and any(marker in question_text.lower() for marker in noise_markers):
            return ""
        return fallback

    return "\n".join(cleaned_lines).strip()


def _normalize_option_text(raw_option: str) -> str:
    """Normalize a possible answer option and remove trailing UI artifacts."""
    option = (raw_option or "").strip()
    if not option:
        return ""

    option = re.sub(r"(?i)^\s*(?:option|options|choice|choices)\s*:\s*", "", option)
    option = re.sub(r"^(?:[A-Da-d]|\d{1,2})[\)\.\-:]\s*", "", option)
    option = re.sub(r"^\s*[-•]?\s*\[\s*[xX ]?\s*\]\s*", "", option)
    option = re.sub(r"(?i)\bq\s*search\b", "", option)
    option = re.sub(r"(?i)\bsearch\b", "", option)
    option = re.sub(r"(?i)\s*(?:tomorrow\s+)?[qgo0]uestion\s+\d+\s*(?:of|/)\s*\d+\b.*$", "", option)
    option = re.sub(r"(?i)\bqsearch\b.*$", "", option)
    option = re.sub(r"(?i)\bsearch\b.*$", "", option)
    option = re.sub(r"(?i)\bsubmit\s*answer\b.*$", "", option)
    option = re.sub(r"(?i)\b(?:tempstorise|temporise)\b.*$", "", option)
    option = re.sub(
        r"(?i)\s+(?:submit\s*answer|tempstorise|temporise|next\s+\w+|\d{1,2}:\d{2}\s*(?:am|pm)|\d{1,2}\s*(?:am|pm)|\d{1,3}\s*°\s*[fc]|q\s*search).*$",
        "",
        option,
    )
    option = re.sub(r"\s+", " ", option).strip(" -|:\n\t")
    if re.fullmatch(r"(?i)\d{1,2}:\d{2}\s*(?:AM|PM)?", option):
        return ""
    if re.fullmatch(r"(?i)\d{1,2}\s*(?:AM|PM)", option):
        return ""
    if re.search(r"(?i)\b\d+\s*x\s*\d+\b", option):
        return ""
    if re.search(r"(?i)\b\d+(?:\.\d+)?\s*(?:kb|mb|gb)\b", option):
        return ""
    if re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{2,4}\s*", option):
        return ""
    if re.fullmatch(r"(?i)\d{1,3}\s*°\s*[fc](?:\s+\w+)?", option):
        return ""
    if re.fullmatch(r"(?i)of\s+\d{1,3}(?:\s+of\s+\d{1,3})?", option):
        return ""
    if re.fullmatch(r"%\s*\d{1,3}", option):
        return ""
    return option


def _build_structured_question_with_options(raw_text: str) -> str:
    """Extract a clean question stem with options from OCR text."""
    cleaned = _clean_extracted_question_text(raw_text)
    if not cleaned:
        return ""

    option_prefix = re.compile(r"^(?:[A-Da-d]|\d{1,2})[\)\.\-:]\s*")
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]

    explicit_options: list[str] = []
    non_option_lines: list[str] = []
    for line in lines:
        lower = line.lower()
        if lower in {"option:", "options:", "choices:", "choice:"}:
            continue
        if option_prefix.match(line):
            opt = _normalize_option_text(line)
            if opt:
                explicit_options.append(opt)
            continue
        if re.match(r"^[-•]\s+", line):
            opt = _normalize_option_text(re.sub(r"^[-•]\s+", "", line))
            if opt:
                explicit_options.append(opt)
            continue
        non_option_lines.append(line)

    non_option_blob = " ".join(non_option_lines).strip()
    stem = ""
    tail = ""
    if "?" in non_option_blob:
        stem_part, tail = non_option_blob.split("?", 1)
        stem = f"{stem_part.strip()}?"
    elif non_option_lines:
        stem = non_option_lines[0]
        tail = " ".join(non_option_lines[1:])

    stem = re.sub(r"(?i)^\s*question\s*:\s*", "", stem).strip()
    stem = re.sub(r"(?i)^\s*question\s+\d+\s*(?:[\)\.\-:]?\s*)", "", stem).strip()
    stem = re.sub(r"^\s*\d{2,3}\s*[\)\.\-:]\s*", "", stem).strip()
    stem = re.sub(r"(?i)\bq\s*search\b", "", stem).strip()
    stem = re.sub(r"(?i)^\s*\d{1,3}\s*°\s*[fc]\b", "", stem).strip()
    stem = re.sub(
        r"(?i)^\s*(?:sunny|cloudy|rainy|stormy|windy|snowy|clear|overcast)\b",
        "",
        stem,
    ).strip()
    stem = re.sub(r"(?i)\bsubmit\s*answer\b.*$", "", stem).strip()
    stem = re.sub(r"(?i)\b(?:tempstorise|temporise)\b.*$", "", stem).strip()
    stem = re.sub(r"\s+", " ", stem).strip()
    if re.fullmatch(r"(?i)of\s+\d{1,4}\b", stem):
        stem = ""

    inline_options: list[str] = []
    inline_source = tail.strip()
    if inline_source:
        inline_source = re.split(
            r"(?i)\b(?:submit\s*answer|tempstorise|temporise|q\s*search|next\s+\w+)\b",
            inline_source,
            maxsplit=1,
        )[0].strip()
        chunks = re.split(r"\s(?:-|–|—|•)\s+", inline_source)
        if len(chunks) > 1:
            for chunk in chunks:
                opt = _normalize_option_text(chunk)
                if opt:
                    inline_options.append(opt)
        else:
            opt = _normalize_option_text(inline_source)
            if opt:
                inline_options.append(opt)

    options: list[str] = []
    seen: set[str] = set()
    for candidate in [*explicit_options, *inline_options]:
        key = candidate.casefold()
        if len(candidate) < 3 and not re.fullmatch(
            r"\$?\d+(?:,\d{3})*(?:\.\d+)?%?",
            candidate.strip(),
        ):
            continue
        if key in seen:
            continue
        seen.add(key)
        options.append(candidate)

    if stem and options:
        return stem + "\nOptions:\n" + "\n".join(f"- {opt}" for opt in options)
    if stem:
        return stem

    # Last resort: return cleaned text if parsing fails.
    return cleaned


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

    # Spacing recovery: RapidOCR can return collapsed words for English UI text.
    if text and _looks_collapsed_text(text):
        print("⚠️ OCR text appears collapsed; trying pytesseract spacing recovery.")
        tesseract_text = _run_pytesseract_ocr(image_path)
        if tesseract_text and _space_ratio(tesseract_text) > _space_ratio(text):
            text = tesseract_text

    # Final fallback: pytesseract (useful when prior OCR returns no text)
    if not text:
        print("⚠️ RapidOCR produced no text. Trying pytesseract fallback.")
        text = _run_pytesseract_ocr(image_path)
        if not text:
            return ""

    text = _normalize_ocr_text(text)

    if not text.strip():
        print("⚠️ OCR produced no usable text.")
        return ""

    print("\n🧾 [OCR Raw Output]\n", text[:1000], "\n")

    return _build_structured_question_with_options(text)


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
def reason_answer(
    question: str,
    context: str,
    used_ids=None,
    *,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
) -> str:
    used_ids = used_ids or []
    try:
        global local_cag_agent
        if local_cag_agent is None:
            local_cag_agent = CAGAgent()
        runtime = local_cag_agent.resolve_reasoning_runtime(
            platform=platform,
            model=model,
            ollama_target=ollama_target,
        )

        return local_cag_agent._generate_answer_with_context(
            question,
            context,
            runtime={
                "platform": runtime.get("platform"),
                "model": runtime.get("model") or REASON_MODEL,
                "ollama_target": runtime.get("ollama_target"),
                "ollama_host": runtime.get("ollama_host"),
                "ollama_api_key": runtime.get("ollama_api_key"),
            },
        )
    except Exception as exc:
        return f"⚠️ No valid output from reasoning model. ({exc})"


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
    profile_id: str | None = None,
) -> dict:
    payload: dict[str, str] = {}
    if platform:
        payload["platform"] = platform.strip()
    if model:
        payload["model"] = model.strip()
    if ollama_target:
        payload["ollama_target"] = ollama_target.strip()
    if profile_id:
        payload["profile_id"] = profile_id.strip()
    return payload


def _extract_cli_answer_sections(answer_text: str) -> dict[str, str]:
    """Normalize free-form model output into Answer/Rationale/Citations."""
    sections = {"Answer": "", "Rationale": "", "Citations": ""}
    current: str | None = None

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


def _build_cli_qa_record(question: str, answer_text: str) -> dict[str, str]:
    sections = _extract_cli_answer_sections(answer_text)
    cleaned_question = _clean_extracted_question_text(question or "")
    return {
        "question": cleaned_question,
        "answer": sections["Answer"],
        "rationale": sections["Rationale"],
        "citations": sections["Citations"],
    }


def _print_cli_qa(question: str, answer_text: str) -> dict[str, str]:
    record = _build_cli_qa_record(question, answer_text)
    cleaned_question = record["question"]
    answer = record["answer"]
    rationale = record["rationale"]
    citations = record["citations"]
    print(f"Question: {cleaned_question}\n")
    print(f"Answer: {answer}\n")
    print(f"Rationale: {rationale}\n")
    print(f"Citations: {citations}\n")
    return record


def _call_remote_cag(
    question: str,
    url: str,
    *,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
    profile_id: str | None = None,
) -> dict:
    url = _validate_remote_http_url(url, field_name="remote_cag_url")
    payload = {"question": question}
    payload.update(_build_runtime_payload(platform, model, ollama_target, profile_id))
    resp = requests.post(
        url,
        json=payload,
        headers=_build_remote_headers() or None,
        timeout=60,
        allow_redirects=False,
    )
    resp.raise_for_status()
    data = _parse_remote_json_response(resp, url)
    return data


def _parse_remote_json_response(resp: requests.Response, url: str) -> dict:
    """Parse remote JSON responses with actionable diagnostics on failure."""
    content_type = (resp.headers.get("Content-Type") or "").lower()
    text = (resp.text or "").strip()
    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        snippet = text[:300].replace("\n", " ")
        raise RuntimeError(
            "Remote endpoint did not return valid JSON "
            f"(url={url}, status={resp.status_code}, content_type={content_type or 'unknown'}, "
            f"body_snippet={snippet!r}, error={exc})"
        ) from exc
    if not isinstance(data, dict):
        snippet = text[:300].replace("\n", " ")
        raise RuntimeError(
            "Remote endpoint returned JSON but not an object "
            f"(url={url}, status={resp.status_code}, content_type={content_type or 'unknown'}, "
            f"body_snippet={snippet!r})"
        )
    return data


def _build_remote_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    remote_api_token = (os.getenv("REMOTE_API_TOKEN") or "").strip()
    if remote_api_token:
        headers["X-API-Key"] = remote_api_token
    return headers


def _validate_remote_http_url(url: str, *, field_name: str) -> str:
    normalized = (url or "").strip()
    if not normalized:
        raise RuntimeError(f"{field_name} is required for remote mode")
    allowed, reason = validate_outbound_url(
        normalized,
        allow_private_networks=VISION_ALLOW_PRIVATE_REMOTE_URLS,
    )
    if not allowed:
        raise RuntimeError(f"{field_name} blocked: {reason}")
    return normalized


def _print_ascii_qr(data: str) -> bool:
    try:
        import qrcode
    except Exception:
        return False
    try:
        qr = qrcode.QRCode(border=1)
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        print("Session QR (scan with phone):")
        for row in matrix:
            line = "".join("██" if cell else "  " for cell in row)
            print(line)
        print("")
        return True
    except Exception:
        return False


def _write_qr_png(data: str, output_path: Path) -> Path | None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import qrcode

        img = qrcode.make(data)
        img.save(output_path)
        return output_path
    except Exception:
        pass

    # Fallback to qrencode CLI if available.
    qrencode = shutil.which("qrencode")
    if not qrencode:
        return None
    try:
        result = subprocess.run(
            [qrencode, "-o", str(output_path), data],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and output_path.exists():
            return output_path
    except Exception:
        return None
    return None


def _capture_session_start_url(remote_image_url: str) -> str:
    remote_image_url = _validate_remote_http_url(
        remote_image_url,
        field_name="remote_image_url",
    )
    parsed = urlparse(remote_image_url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(f"Invalid remote image URL: {remote_image_url}")
    return f"{parsed.scheme}://{parsed.netloc}/capture-session/start"


def _create_remote_capture_session(
    remote_image_url: str,
    *,
    ttl_minutes: int | None = None,
) -> dict:
    payload: dict[str, int] = {}
    if ttl_minutes is not None:
        payload["ttl_minutes"] = int(ttl_minutes)
    start_url = _capture_session_start_url(remote_image_url)
    resp = requests.post(
        start_url,
        json=payload or None,
        headers=_build_remote_headers() or None,
        timeout=30,
        allow_redirects=False,
    )
    resp.raise_for_status()
    data = _parse_remote_json_response(resp, start_url)
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "Failed to create remote capture session"))
    required = ("session_id", "access_code", "access_url")
    missing = [k for k in required if not str(data.get(k) or "").strip()]
    if missing:
        raise RuntimeError(f"Remote capture session response missing fields: {', '.join(missing)}")
    return data


def _write_qr_popup_page(
    *,
    page_url: str,
    access_code: str,
    session_id: str,
    expires_at: str | None,
    output_dir: Path,
) -> tuple[Path | None, Path | None]:
    output_dir.mkdir(parents=True, exist_ok=True)
    qr_png = output_dir / f"capture_session_{session_id}_qr.png"
    qr_written = _write_qr_png(page_url, qr_png)
    qr_src = qr_png.name if qr_written else ""

    html_path = output_dir / f"capture_session_{session_id}_qr.html"
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Capture Session QR</title>
    <style>
      body {{
        margin: 0;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        background: #061229;
        color: #e6eeff;
      }}
      .wrap {{ max-width: 680px; margin: 0 auto; padding: 20px; }}
      .card {{
        border: 1px solid #2b4778;
        border-radius: 14px;
        background: #0d1b33;
        padding: 16px;
      }}
      .row {{ margin: 10px 0; }}
      .label {{ font-weight: 700; color: #cfe0ff; margin-bottom: 4px; }}
      .value {{ word-break: break-all; white-space: pre-wrap; }}
      img {{
        display: block;
        width: min(92vw, 420px);
        height: auto;
        background: #fff;
        border-radius: 10px;
        padding: 12px;
        margin: 8px auto;
      }}
    </style>
  </head>
  <body>
    <div class="wrap">
      <div class="card">
        <div class="row"><div class="label">Session ID</div><div class="value">{session_id}</div></div>
        <div class="row"><div class="label">Access Code (enter on phone)</div><div class="value">{access_code}</div></div>
        <div class="row"><div class="label">VPS Session URL</div><div class="value">{page_url}</div></div>
        <div class="row"><div class="label">Expires At (UTC)</div><div class="value">{expires_at or "N/A"}</div></div>
        {f'<img src="{qr_src}" alt="Session QR" />' if qr_src else '<div class="row"><div class="value">QR PNG unavailable.</div></div>'}
      </div>
    </div>
  </body>
</html>
"""
    html_path.write_text(html, encoding="utf-8")
    return qr_written, html_path


def _answer_from_image_path(
    img_path: Path,
    mode: str = "local",
    remote_cag_url: str | None = None,
    remote_mcp_url: str | None = None,
    remote_image_url: str | None = None,
    capture_session_id: str | None = None,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
    profile_id: str | None = None,
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
            remote_image_url = _validate_remote_http_url(
                remote_image_url,
                field_name="remote_image_url",
            )
            runtime_payload = _build_runtime_payload(platform, model, ollama_target, profile_id)
            if capture_session_id:
                runtime_payload["capture_session_id"] = capture_session_id
            with open(img_path, "rb") as f:
                resp = requests.post(
                    remote_image_url,
                    files={"image": f},
                    data=runtime_payload or None,
                    headers=_build_remote_headers() or None,
                    timeout=120,
                    allow_redirects=False,
                )
            resp.raise_for_status()
            data = _parse_remote_json_response(resp, remote_image_url)
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
            remote_cag_url = _validate_remote_http_url(
                remote_cag_url,
                field_name="remote_cag_url",
            )
            remote = _call_remote_cag(
                q_text,
                remote_cag_url,
                platform=platform,
                model=model,
                ollama_target=ollama_target,
                profile_id=profile_id,
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
    context = local_cag_agent.enhanced_retrieve_context(
        q_text, top_k=12, profile_id=profile_id
    )
    used_ids: list[str] = []
    snippet = context[:500] + ("…" if len(context) > 500 else "")
    ans_block = reason_answer(
        q_text,
        context,
        used_ids,
        platform=platform,
        model=model,
        ollama_target=ollama_target,
    )

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
    profile_id: str | None = None,
) -> dict:
    img_path = capture_monitor(
        monitor_index or TARGET_MONITOR,
        top_offset=top_offset,
        bottom_offset=bottom_offset,
        left_offset=left_offset,
        right_offset=right_offset,
        region=region,
    )
    return _answer_from_image_path(
        img_path=img_path,
        mode=mode,
        remote_cag_url=remote_cag_url,
        remote_mcp_url=remote_mcp_url,
        remote_image_url=remote_image_url,
        platform=platform,
        model=model,
        ollama_target=ollama_target,
        profile_id=profile_id,
    )


def run_image_once(
    image_path: str | Path,
    mode: str = "local",
    remote_cag_url: str | None = None,
    remote_mcp_url: str | None = None,
    remote_image_url: str | None = None,
    platform: str | None = None,
    model: str | None = None,
    ollama_target: str | None = None,
    profile_id: str | None = None,
) -> dict:
    img_path = Path(image_path).expanduser().resolve()
    if not img_path.exists():
        raise FileNotFoundError(f"Image not found: {img_path}")
    return _answer_from_image_path(
        img_path=img_path,
        mode=mode,
        remote_cag_url=remote_cag_url,
        remote_mcp_url=remote_mcp_url,
        remote_image_url=remote_image_url,
        platform=platform,
        model=model,
        ollama_target=ollama_target,
        profile_id=profile_id,
    )


# ---------------- INTERACTIVE LOOP (CLI use) ----------------
REMOTE_MCP_URL = os.getenv("REMOTE_MCP_URL")
REMOTE_MODE = (os.getenv("REMOTE_MODE") or "local").lower()
REMOTE_CAG_URL = os.getenv("REMOTE_CAG_URL")
REMOTE_IMAGE_URL = os.getenv("REMOTE_IMAGE_URL")
REMOTE_PROFILE_ID = (os.getenv("STUDY_AGENTS_PROFILE_ID") or os.getenv("PROFILE_ID") or "").strip() or None


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
    profile_id: str | None = REMOTE_PROFILE_ID,
    session_web: bool = True,
    session_web_open: bool = True,
    session_web_ttl_minutes: int | None = None,
    session_web_qr: bool = True,
    session_web_qr_ascii: bool = False,
):
    effective_mode = (mode or REMOTE_MODE or "local").lower()
    effective_remote_cag = remote_cag_url or REMOTE_CAG_URL
    effective_remote_image = REMOTE_IMAGE_URL

    if keyboard is None:
        raise RuntimeError(
            "Interactive capture loop requires the 'keyboard' package. "
            "Install optional extras with `pip install study-agents[vision]`."
        )

    remote_capture_session_id: str | None = None
    if effective_mode == "remote_image" and session_web:
        if not effective_remote_image:
            print("⚠️ Session web mode requested, but REMOTE_IMAGE_URL is not configured.")
        else:
            try:
                created = _create_remote_capture_session(
                    effective_remote_image,
                    ttl_minutes=session_web_ttl_minutes,
                )
                remote_capture_session_id = str(created.get("session_id") or "").strip()
                session_url = str(created.get("access_url") or "").strip()
                access_code = str(created.get("access_code") or "").strip()
                expires_at = str(created.get("expires_at") or "").strip()
                if remote_capture_session_id and session_url and access_code:
                    print(f"Session report URL (VPS): {session_url}")
                    print(f"Session access code: {access_code}")
                    if expires_at:
                        print(f"Session expires (UTC): {expires_at}")
                    if session_web_qr:
                        qr_png, qr_html = _write_qr_popup_page(
                            page_url=session_url,
                            access_code=access_code,
                            session_id=remote_capture_session_id,
                            expires_at=expires_at,
                            output_dir=SCREENSHOT_DIR / "capture_sessions",
                        )
                        if qr_png:
                            print(f"Session QR PNG: {qr_png}")
                        else:
                            print("Session QR PNG: unavailable (install `qrcode[pil]` for automatic QR generation).")
                        if qr_html:
                            print(f"Session QR page: {qr_html}")
                            if session_web_open:
                                with contextlib.suppress(Exception):
                                    webbrowser.open(qr_html.resolve().as_uri())
                        if session_web_qr_ascii:
                            _print_ascii_qr(session_url)
                else:
                    print("⚠️ Remote session creation returned incomplete data; continuing without session page.")
            except Exception as exc:
                print(f"⚠️ Remote session setup failed: {exc}")

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
                    runtime_payload = _build_runtime_payload(
                        platform, model, ollama_target, profile_id
                    )
                    if remote_capture_session_id:
                        runtime_payload["capture_session_id"] = remote_capture_session_id
                    with open(img_path, "rb") as f:
                        resp = requests.post(
                            effective_remote_image,
                            files={"image": f},
                            data=runtime_payload or None,
                            headers=_build_remote_headers() or None,
                            timeout=120,
                            allow_redirects=False,
                        )
                    resp.raise_for_status()
                    data = _parse_remote_json_response(resp, effective_remote_image)
                    remote_question = (data.get("question") or "").strip()
                    _print_cli_qa(remote_question, data.get("answer", ""))
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

            if effective_mode == "remote" and effective_remote_cag:
                try:
                    remote = _call_remote_cag(
                        q_text,
                        effective_remote_cag,
                        platform=platform,
                        model=model,
                        ollama_target=ollama_target,
                        profile_id=profile_id,
                    )
                    _print_cli_qa(remote.get("question", q_text), remote.get("answer", ""))
                except Exception as exc:
                    print(f"⚠️ Remote CAG call failed: {exc}\n")
            else:
                # Default local path: use enhanced CAG retrieval (vector + KG)
                global local_cag_agent
                if local_cag_agent is None:
                    local_cag_agent = CAGAgent()
                context = local_cag_agent.enhanced_retrieve_context(
                    q_text, top_k=12, profile_id=profile_id
                )
                used_ids: list[str] = []

                ans_block = reason_answer(
                    q_text,
                    context,
                    used_ids,
                    platform=platform,
                    model=model,
                    ollama_target=ollama_target,
                )
                _print_cli_qa(q_text, ans_block)

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
        "--profile-id",
        default=REMOTE_PROFILE_ID,
        help="Optional profile namespace to scope retrieval (sent to remote backend).",
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
    parser.add_argument(
        "--no-session-web",
        action="store_true",
        help="Disable the local secure session web page in remote_image mode.",
    )
    parser.add_argument(
        "--no-session-web-open",
        action="store_true",
        help="Do not auto-open the local QR popup page.",
    )
    parser.add_argument(
        "--session-web-ttl-minutes",
        type=int,
        default=120,
        help="Temporary VPS capture session lifetime in minutes (default: 120).",
    )
    parser.add_argument(
        "--no-session-web-qr",
        action="store_true",
        help="Disable QR generation for the session URL.",
    )
    parser.add_argument(
        "--session-web-qr-ascii",
        action="store_true",
        help="Also print an ASCII QR in the terminal (best effort).",
    )

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
        profile_id=args.profile_id,
        session_web=not args.no_session_web,
        session_web_open=not args.no_session_web_open,
        session_web_ttl_minutes=args.session_web_ttl_minutes,
        session_web_qr=not args.no_session_web_qr,
        session_web_qr_ascii=args.session_web_qr_ascii,
    )
