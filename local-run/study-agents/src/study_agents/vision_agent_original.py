# src/study_agents/vision_agent.py

import os
import time
from datetime import datetime
from pathlib import Path

import keyboard
import mss
import mss.tools
from docling.document_converter import DocumentConverter
from openai import OpenAI

from .ollama_client import chat as ollama_chat
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
from .supabase_client import get_supabase_client

# --- ENV for Ollama Cloud/local ---
os.environ["OLLAMA_HOST"] = "https://ollama.com"
if OLLAMA_API_KEY:
    os.environ["OLLAMA_API_KEY"] = OLLAMA_API_KEY

# --- CLIENTS ---
supabase = get_supabase_client()
openai_client = OpenAI(api_key=OPENAI_API_KEY)
docling = DocumentConverter()

# ---------------- REASONING ROLE ----------------
REASONING_SYSTEM = """You are a licensed Texas Property & Casualty Insurance Agent and All-Lines Adjuster.
Use ONLY the retrieved CONTEXT to answer the question extracted from the screenshot.
If the context is insufficient, respond exactly: INSUFFICIENT_CONTEXT.
Provide reasoning concisely with Texas-specific relevance.

Format:
Answer: <concise answer>
Rationale: <brief justification>
Citations: <qa_id(s)>
"""


# ---------------- SCREEN CAPTURE ----------------
def capture_monitor(monitor_index: int = 1) -> Path:
    """
    Capture a screenshot of the configured monitor (with a small top offset)
    and save it into SCREENSHOT_DIR.
    """
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        monitor = sct.monitors[monitor_index]

        top_offset = 100
        bottom_offset = 0
        left_offset = 0
        right_offset = 0

        region = {
            "top": monitor["top"] + top_offset,
            "left": monitor["left"] + left_offset,
            "width": monitor["width"] - right_offset,
            "height": monitor["height"] - top_offset - bottom_offset,
        }

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"docling_capture_{ts}.png"
        sct_img = sct.grab(region)
        mss.tools.to_png(sct_img.rgb, sct_img.size, output=str(path))
        return path


# ---------------- OCR + QUESTION EXTRACTION ----------------
def extract_question_with_docling(image_path: Path) -> str:
    """
    Extracts text from a screenshot using Docling OCR and falls back to RapidOCR if Docling returns nothing.
    Compatible with older Docling versions (no fmt argument).
    """
    text = ""
    try:
        result = docling.convert(str(image_path))  # NOTE: no fmt argument

        # Try all known Docling structures for text extraction
        if hasattr(result, "document"):
            doc = result.document
            if hasattr(doc, "text_content") and doc.text_content:
                text = doc.text_content
            elif hasattr(doc, "text") and doc.text:
                text = doc.text
            elif hasattr(doc, "pages"):
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
    emb = openai_client.embeddings.create(model=OPENAI_EMBED_MODEL, input=text)
    return emb.data[0].embedding


def retrieve_context(question: str, threshold: float = 0.1, k: int = 10):
    embedding = embed_text(question)
    res = supabase.rpc(
        "match_documents",
        {
            "query_embedding": embedding,
            "match_threshold": threshold,
            "match_count": k,
        },
    ).execute()

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
        return "⚠️ INSUFFICIENT_CONTEXT"
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


# ---------------- ONE-SHOT ENTRYPOINT (for MCP) ----------------
def run_capture_once() -> dict:
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
    img_path = capture_monitor(TARGET_MONITOR)

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

    context, pairs = retrieve_context(q_text, threshold=0.08, k=12)
    used_ids = [pid for pid, _ in pairs]
    snippet = context[:500] + ("…" if len(context) > 500 else "")
    ans_block = reason_answer(q_text, context, used_ids)

    return {
        "ok": True,
        "question": q_text,
        "answer": ans_block,
        "context_ids": used_ids,
        "context_snippet": snippet,
        "screenshot_path": str(img_path),
    }


# ---------------- INTERACTIVE LOOP (CLI use) ----------------
def main_loop():
    print("📄 Docling Capture Agent active...")
    print("Press 'Z' to capture and process current monitor. Press 'Esc' to exit.")

    seen = set()

    while True:
        if keyboard.is_pressed("esc"):
            print("\n🛑 Docling agent stopped.")
            break

        if keyboard.is_pressed("z"):
            time.sleep(0.15)
            print("\n📸 Capturing screenshot...")
            img_path = capture_monitor(TARGET_MONITOR)

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

            context, pairs = retrieve_context(q_text, threshold=0.08, k=12)
            used_ids = [pid for pid, _ in pairs]
            if pairs:
                for pid, sim in pairs[:5]:
                    print(f"→ {pid} | sim={sim:.3f}")
            else:
                print("→ No matches from the vector store.")

            if context:
                snippet = context[:500] + ("…" if len(context) > 500 else "")
                print("\n🧩 Context sample:\n", snippet, "\n")

            ans_block = reason_answer(q_text, context, used_ids)
            print(f"💡 {ans_block}\n")

            time.sleep(0.6)
        time.sleep(0.05)


if __name__ == "__main__":
    main_loop()
