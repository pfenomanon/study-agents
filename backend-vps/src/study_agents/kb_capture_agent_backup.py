# src/study_agents/kb_capture_agent.py

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI

from .config import (
    OLLAMA_API_KEY,
    OLLAMA_HOST,
    REASON_MODEL,
    SCREENSHOT_DIR,
    TARGET_MONITOR,
    OPENAI_API_KEY,
    OPENAI_EMBED_MODEL,
    SUPABASE_URL,
    SUPABASE_KEY,
    SUPABASE_DOCS_TABLE,
)

from .ollama_client import chat as ollama_chat

import keyboard
import mss
import mss.tools
from docling.document_converter import DocumentConverter

# --- CLIENTS ---
docling = DocumentConverter()

# Configure Ollama
if OLLAMA_HOST:
    os.environ.setdefault("OLLAMA_HOST", OLLAMA_HOST)
if OLLAMA_API_KEY:
    os.environ.setdefault("OLLAMA_API_KEY", OLLAMA_API_KEY)

# Load Markdown system prompt
SYSTEM_PROMPT_PATH = (
    Path(__file__).parent.parent.parent / "prompts" / "markdown_system_prompt.md"
)
MARKDOWN_SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") if SYSTEM_PROMPT_PATH.exists() else "Convert the following text to clean, well-structured Markdown:"

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
        path = SCREENSHOT_DIR / f"kb_capture_{ts}.png"
        sct_img = sct.grab(region)
        mss.tools.to_png(sct_img.rgb, sct_img.size, output=str(path))
        return path


# ---------------- TEXT TO MARKDOWN CONVERSION ----------------
def convert_to_markdown(text: str) -> str:
    """
    Convert extracted text to clean Markdown using the reasoning model.
    """
    try:
        result = ollama_chat(
            model=REASON_MODEL,
            messages=[
                {"role": "system", "content": MARKDOWN_SYSTEM_PROMPT},
                {"role": "user", "content": f"Convert this text to Markdown:\n\n{text}"},
            ],
        )
        
        # Handle different ollama client response formats
        if isinstance(result, dict):
            return result.get("message", {}).get("content", text).strip()
        return result.message.content.strip()
    except Exception as e:
        print(f"⚠️ Markdown conversion failed: {e}")
        return text  # Fallback to original text


# ---------------- OCR + TEXT EXTRACTION ----------------
def add_missing_spaces(text: str) -> str:
    """
    Add spaces between words where they're likely missing due to OCR issues.
    This handles common patterns like camelCase, number-word boundaries, etc.
    """
    if not text:
        return text
    
    import re
    
    # Add space before capital letters that follow lowercase letters (camelCase)
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    
    # Add space between numbers and letters
    text = re.sub(r'([0-9])([A-Za-z])', r'\1 \2', text)
    text = re.sub(r'([A-Za-z])([0-9])', r'\1 \2', text)
    
    # Fix common OCR artifacts
    text = text.replace('l0', '10')  # Fix lowercase L + zero
    text = text.replace('O0', '00')  # Fix capital O + zero
    text = text.replace('Thesz', 'These')  # Fix common OCR error
    text = text.replace('Folicy', 'Policy')  # Fix common OCR error
    
    # Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text)
    
    # Clean up space before punctuation
    text = re.sub(r'\s+([.,;:!])', r'\1', text)
    
    return text

def extract_text_with_docling(image_path: Path) -> str:
    """
    Extracts text from a screenshot using EasyOCR directly for better spacing,
    with Docling as fallback for layout analysis.
    
    Note: The official Docling documentation techniques with pipeline_options
    are not compatible with Docling v2.58.0 (missing 'options' parameter).
    """
    text = ""
    
    # Try EasyOCR directly first for best spacing preservation
    try:
        from easyocr import Reader
        
        # Initialize EasyOCR for English
        reader = Reader(['en'], gpu=False)  # CPU-only since no GPU
        result = reader.readtext(str(image_path))
        
        # Extract text with confidence filtering
        texts = []
        for (bbox, extracted_text, confidence) in result:
            if confidence > 0.5:  # Filter low confidence results
                texts.append(extracted_text)
        
        if texts:
            # Join with newlines to preserve layout
            text = "\n".join(texts)
            print("✅ EasyOCR extraction successful")
    except Exception as e:
        print(f"⚠️ EasyOCR failed: {e}")
    
    # Fallback to Docling if EasyOCR fails
    if not text:
        print("⚠️ EasyOCR returned no text. Trying Docling fallback...")
        try:
            result = docling.convert(str(image_path))  # NOTE: no options parameter in v2.58.0

            # Try all known Docling structures for text extraction
            if hasattr(result, "document"):
                doc = result.document
                
                # First try export_to_markdown which preserves formatting best
                try:
                    text = doc.export_to_markdown()
                    # Remove image placeholders and clean up
                    if text == "<!-- image -->":
                        text = ""
                    elif text.strip():
                        # Clean up common OCR artifacts
                        text = text.replace('<!-- image -->', '').strip()
                except:
                    pass
                
                # If markdown didn't work, try export_to_text
                if not text:
                    try:
                        text = doc.export_to_text()
                        if text.strip():
                            text = text.strip()
                    except:
                        pass
                
                # Fallback to texts attribute (less formatting)
                if not text and hasattr(doc, "texts") and doc.texts:
                    text = "\n".join([t.text for t in doc.texts if hasattr(t, "text") and t.text])
                
                # Final fallback to other methods
                elif not text and hasattr(doc, "text_content") and doc.text_content:
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
                    # Try export_to_markdown first
                    try:
                        md = doc.export_to_markdown()
                        if md and md != "<!-- image -->":
                            text += md + "\n"
                    except:
                        pass
                    
                    # Try export_to_text
                    try:
                        txt = doc.export_to_text()
                        if txt.strip():
                            text += txt + "\n"
                    except:
                        pass
                    
                    # Fallback to texts attribute
                    if not text and hasattr(doc, "texts") and doc.texts:
                        text += "\n".join([t.text for t in doc.texts if hasattr(t, "text") and t.text]) + "\n"
                    elif not text and hasattr(doc, "text") and doc.text:
                        text += doc.text + "\n"
                    elif not text and hasattr(doc, "pages"):
                        for page in doc.pages:
                            if hasattr(page, "text") and page.text:
                                text += page.text + "\n"

            text = text.strip()

        except Exception as e:
            print(f"⚠️ Docling extraction error: {e}")

    # Final fallback to RapidOCR
    if not text:
        print("⚠️ Both EasyOCR and Docling failed. Trying RapidOCR as last resort...")
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

    # Post-process to add missing spaces (only if not using EasyOCR)
    if "EasyOCR extraction successful" not in str(text) and "Docling" in str(text):
        text = add_missing_spaces(text)
    
    print("\n🧾 [OCR Raw Output]\n", text[:1000], "\n")
    return text.strip()


# ---------------- KB APPEND ----------------
def append_to_knowledge_base(text: str, kb_path: Optional[Path] = None) -> Path:
    """
    Append extracted text as a Markdown entry to the knowledge base file.
    Each entry includes a timestamp and a horizontal rule.
    """
    if kb_path is None:
        kb_path = Path("knowledge_base.md")
    kb_path = kb_path.resolve()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n---\n\n## {timestamp}\n\n{text.strip()}\n"
    with kb_path.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"📝 Appended to {kb_path}")
    return kb_path


# ---------------- INTERACTIVE LOOP ----------------
def main_loop(kb_filename: str = "knowledge_base.md"):
    print(f"📄 Knowledge Base Capture Agent active (saving to: {kb_filename})...")
    print("Press 'Z' to capture screenshot and append text to knowledge base. Press 'Esc' to exit.")

    seen = set()

    while True:
        if keyboard.is_pressed("esc"):
            print("\n🛑 KB capture agent stopped.")
            break

        if keyboard.is_pressed("z"):
            time.sleep(0.15)
            print("\n📸 Capturing screenshot...")
            img_path = capture_monitor(TARGET_MONITOR)

            extracted_text = extract_text_with_docling(img_path)
            if not extracted_text:
                print("⚠️ No readable text found in screenshot.")
                time.sleep(0.5)
                continue
                
            # Convert to Markdown
            markdown_text = convert_to_markdown(extracted_text)
            if not markdown_text:
                print("⚠️ Markdown conversion failed.")
                time.sleep(0.5)
                continue

            if markdown_text in seen:
                print("↩️ Duplicate text detected; skipping.")
                time.sleep(0.5)
                continue
            seen.add(markdown_text)

            print(f"\n📄 Extracted Markdown:\n{markdown_text[:500]}{'...' if len(markdown_text) > 500 else ''}\n")

            # Append Markdown text to knowledge base
            append_to_knowledge_base(markdown_text, Path(kb_filename))

            time.sleep(0.6)
        time.sleep(0.05)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Knowledge Base Capture Agent")
    parser.add_argument(
        "--filename", 
        "-f", 
        default="knowledge_base.md",
        help="Filename for the knowledge base (default: knowledge_base.md)"
    )
    args = parser.parse_args()
    main_loop(args.filename)
