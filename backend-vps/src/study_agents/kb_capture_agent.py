# src/study_agents/kb_capture_agent.py

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI
import json
import uuid

# Global variables
global TARGET_MONITOR

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

# Import RAG modules for chunking
from .rag_builder_core import chunk_text, split_into_paragraphs
from .ollama_client import chat as ollama_chat
from .prompt_loader import load_prompt
from .supabase_client import create_supabase_client

try:
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
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    EasyOcrOptions,
    TesseractOcrOptions,
    RapidOcrOptions,
)

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
MARKDOWN_SYSTEM_PROMPT = (
    SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    if SYSTEM_PROMPT_PATH.exists()
    else "Convert the following text to clean, well-structured Markdown:"
)

_DEFAULT_KB_PRIMARY_PROMPT = """You are a precise, evidence-based assistant. Follow these rules:

1. If the context provides the answer, use it directly
2. If the context doesn't contain the answer, use your general knowledge
3. For True/False questions, answer based on standard practices and common knowledge
4. Keep answers concise and factual
5. For multiple choice questions, select the most appropriate answer"""

_DEFAULT_KB_FALLBACK_PROMPT = "You are a helpful assistant. Answer the following question."

KB_PRIMARY_PROMPT = load_prompt("kb_answer_primary.txt", _DEFAULT_KB_PRIMARY_PROMPT)
KB_FALLBACK_PROMPT = load_prompt("kb_answer_fallback.txt", _DEFAULT_KB_FALLBACK_PROMPT)

# ---------------- SCREEN CAPTURE ----------------
def capture_monitor(
    monitor_index: int = 1,
    region: dict = None,
    top_offset: int | None = None,
    bottom_offset: int | None = None,
    left_offset: int | None = None,
    right_offset: int | None = None,
) -> Path:
    """
    Capture a screenshot of the specified monitor with optional custom region.
    If region is None, captures the entire monitor (with default top offset).
    """
    if mss is None or mss_tools is None:
        raise RuntimeError(
            "Screen capture requires optional dependencies. "
            "Install with `pip install study-agents[vision]`."
        )

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        # List all available monitors first
        print("\n📺 Available Monitors:")
        for i, mon in enumerate(sct.monitors):
            print(f"   Monitor {i}: {mon['width']}x{mon['height']} at ({mon['left']}, {mon['top']})")

        # Ensure monitor_index is within valid range
        if monitor_index < 0 or monitor_index >= len(sct.monitors):
            print(f"⚠️ Warning: Monitor {monitor_index} is out of range. Using primary monitor (0).")
            monitor_index = 0

        monitor = sct.monitors[monitor_index]
        print(f"\n🎯 Selected Monitor {monitor_index}: {monitor['width']}x{monitor['height']} at ({monitor['left']}, {monitor['top']})")
        
        if region:
            # Use custom region relative to the selected monitor
            capture_region = {
                "top": monitor["top"] + region.get("y", 0),
                "left": monitor["left"] + region.get("x", 0),
                "width": region.get("width", monitor["width"]),
                "height": region.get("height", monitor["height"]),
            }
            print(f"   Custom Region: x={region.get('x', 0)}, y={region.get('y', 0)}, "
                  f"w={region.get('width', monitor['width'])}, h={region.get('height', monitor['height'])}")
        else:
            # Default behavior - entire monitor with optional offsets
            capture_region = {
                "top": monitor["top"],
                "left": monitor["left"],
                "width": monitor["width"],
                "height": monitor["height"],
            }
            default_top = 100 if top_offset is None else 0
            offsets = {
                "top": top_offset if top_offset is not None else default_top,
                "bottom": bottom_offset or 0,
                "left": left_offset or 0,
                "right": right_offset or 0,
            }
            capture_region["top"] += offsets["top"]
            capture_region["left"] += offsets["left"]
            capture_region["width"] -= offsets["left"] + offsets["right"]
            capture_region["height"] -= offsets["top"] + offsets["bottom"]
            capture_region["width"] = max(10, capture_region["width"])
            capture_region["height"] = max(10, capture_region["height"])
            print(
                "   Region offsets (px): "
                f"top={offsets['top']}, bottom={offsets['bottom']}, "
                f"left={offsets['left']}, right={offsets['right']}"
            )

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOT_DIR / f"kb_capture_{ts}.png"
        print(f"\n📸 Capturing from: top={capture_region['top']}, left={capture_region['left']}, "
              f"width={capture_region['width']}, height={capture_region['height']}")
        
        sct_img = sct.grab(capture_region)
        mss_tools.to_png(sct_img.rgb, sct_img.size, output=str(path))
        return path


# ---------------- TEXT TO MARKDOWN CONVERSION ----------------
def convert_to_markdown(text: str) -> str:
    """
    Convert extracted text to clean Markdown using the reasoning model.
    """
    try:
        if REASON_MODEL.startswith("gpt"):
            # Use OpenAI for GPT models
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": MARKDOWN_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Convert this text to Markdown:\n\n{text}"},
                ],
                temperature=0.1,
            )
            return response.choices[0].message.content.strip()
        else:
            # Use Ollama for other models
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
    Extracts text from a screenshot using Docling with proper OCR backend configuration.
    Now supports the latest Docling features including pipeline options.
    """
    text = ""
    
    # Try EasyOCR backend first (best for English)
    try:
        print("🔧 Configuring Docling with EasyOCR backend...")
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = True
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        
        # Configure EasyOCR options
        ocr_options = EasyOcrOptions(force_full_page_ocr=True)
        ocr_options.lang = ["en"]  # Set language to English
        pipeline_options.ocr_options = ocr_options
        
        converter = DocumentConverter(
            format_options={
                InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options),
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            }
        )
        
        result = converter.convert(str(image_path))
        
        # Extract text using the new API
        if hasattr(result, "document"):
            doc = result.document
            text = doc.export_to_markdown()
            # Clean up image placeholders
            if text == "<!-- image -->":
                text = ""
            elif text.strip():
                text = text.replace('<!-- image -->', '').strip()
        
        if text:
            print("✅ Docling with EasyOCR backend successful")
    except Exception as e:
        print(f"⚠️ Docling EasyOCR backend failed: {e}")
    
    # Try Tesseract backend if EasyOCR fails
    if not text:
        try:
            print("🔧 Trying Docling with Tesseract backend...")
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True
            
            ocr_options = TesseractOcrOptions(force_full_page_ocr=True)
            pipeline_options.ocr_options = ocr_options
            
            converter = DocumentConverter(
                format_options={
                    InputFormat.IMAGE_PNG: PdfFormatOption(pipeline_options=pipeline_options),
                    InputFormat.IMAGE_JPEG: PdfFormatOption(pipeline_options=pipeline_options),
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
            
            result = converter.convert(str(image_path))
            
            if hasattr(result, "document"):
                doc = result.document
                text = doc.export_to_markdown()
                if text == "<!-- image -->":
                    text = ""
                elif text.strip():
                    text = text.replace('<!-- image -->', '').strip()
            
            if text:
                print("✅ Docling with Tesseract backend successful")
        except Exception as e:
            print(f"⚠️ Docling Tesseract backend failed: {e}")
    
    # Try RapidOCR backend as fallback
    if not text:
        try:
            print("🔧 Trying Docling with RapidOCR backend...")
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True
            
            ocr_options = RapidOcrOptions(force_full_page_ocr=True)
            pipeline_options.ocr_options = ocr_options
            
            converter = DocumentConverter(
                format_options={
                    InputFormat.IMAGE_PNG: PdfFormatOption(pipeline_options=pipeline_options),
                    InputFormat.IMAGE_JPEG: PdfFormatOption(pipeline_options=pipeline_options),
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
            
            result = converter.convert(str(image_path))
            
            if hasattr(result, "document"):
                doc = result.document
                text = doc.export_to_markdown()
                if text == "<!-- image -->":
                    text = ""
                elif text.strip():
                    text = text.replace('<!-- image -->', '').strip()
            
            if text:
                print("✅ Docling with RapidOCR backend successful")
        except Exception as e:
            print(f"⚠️ Docling RapidOCR backend failed: {e}")
    
    # Final fallback to direct EasyOCR if all Docling backends fail
    if not text:
        print("⚠️ All Docling backends failed. Trying direct EasyOCR...")
        try:
            from easyocr import Reader
            
            reader = Reader(['en'], gpu=False)
            result = reader.readtext(str(image_path))
            
            texts = []
            for (bbox, extracted_text, confidence) in result:
                if confidence > 0.5:
                    texts.append(extracted_text)
            
            if texts:
                text = "\n".join(texts)
                print("✅ Direct EasyOCR extraction successful")
        except Exception as e:
            print(f"⚠️ Direct EasyOCR failed: {e}")
    
    if not text.strip():
        print("⚠️ OCR produced no usable text.")
        return ""
    
    # Post-process to add missing spaces (only for non-EasyOCR results)
    if "EasyOCR" not in str(text) or "Docling" in str(text):
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


# ---------------- CAG IMPLEMENTATION ----------------
def chunk_extracted_text(text: str, source: str = "kb_capture") -> list[dict]:
    """
    Chunk extracted text using RAG chunking strategy.
    """
    paragraphs = split_into_paragraphs(text)
    chunks = chunk_text(paragraphs, chunk_size=1200, overlap=150)
    
    chunk_dicts = []
    timestamp = datetime.now().isoformat()
    
    for i, chunk in enumerate(chunks):
        chunk_dict = {
            "id": f"{source}_{int(time.time())}_{i:03d}",
            "text": chunk,
            "source": source,
            "timestamp": timestamp,
            "chunk_index": i
        }
        chunk_dicts.append(chunk_dict)
    
    return chunk_dicts


def generate_embeddings(chunks: list[dict]) -> list[dict]:
    """
    Generate embeddings for chunks using OpenAI.
    """
    if REASON_MODEL.startswith("gpt"):
        # Use OpenAI for GPT models
        client = OpenAI(api_key=OPENAI_API_KEY)
        for chunk in chunks:
            try:
                embedding = client.embeddings.create(
                    model=OPENAI_EMBED_MODEL,
                    input=chunk["text"]
                ).data[0].embedding
                chunk["embedding"] = embedding
            except Exception as e:
                print(f"⚠️ Failed to generate embedding for chunk {chunk['id']}: {e}")
                chunk["embedding"] = None
    else:
        # For Ollama models, we still need OpenAI for embeddings
        client = OpenAI(api_key=OPENAI_API_KEY)
        for chunk in chunks:
            try:
                embedding = client.embeddings.create(
                    model=OPENAI_EMBED_MODEL,
                    input=chunk["text"]
                ).data[0].embedding
                chunk["embedding"] = embedding
            except Exception as e:
                print(f"⚠️ Failed to generate embedding for chunk {chunk['id']}: {e}")
                chunk["embedding"] = None
    
    return chunks


def upsert_to_supabase(chunks: list[dict]) -> int:
    """
    Upsert chunks with embeddings to Supabase.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("⚠️ Supabase credentials not configured. Skipping Supabase upload.")
        return 0

    supabase = create_supabase_client()
    count = 0
    
    for chunk in chunks:
        if chunk["embedding"] is None:
            continue
            
        try:
            supabase.table(SUPABASE_DOCS_TABLE).upsert({
                "id": chunk["id"],
                "content": chunk["text"],
                "embedding": chunk["embedding"],
                "meta": {
                    "source": chunk["source"],
                    "timestamp": chunk["timestamp"],
                    "chunk_index": chunk["chunk_index"]
                }
            }).execute()
            count += 1
        except Exception as e:
            print(f"⚠️ Failed to upsert chunk {chunk['id']}: {e}")
    
    return count


def retrieve_context(question: str, top_k: int = 5) -> str:
    """
    Retrieve relevant context from Supabase using vector search.
    """
    try:
        # Generate query embedding
        client = OpenAI(api_key=OPENAI_API_KEY)
        query_embedding = client.embeddings.create(
            model=OPENAI_EMBED_MODEL,
            input=question
        ).data[0].embedding
        
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("⚠️ Supabase credentials not configured. Skipping Supabase search.")
            return ""
        # Search similar chunks
        supabase = create_supabase_client()
        results = supabase.rpc('match_documents', {
            'query_embedding': query_embedding,
            'match_threshold': 0.2,
            'match_count': top_k
        }).execute()
        
        # Format context
        contexts = [r['content'] for r in results.data] if results.data else []
        return "\n\n---\n\n".join(contexts)
        
    except Exception as e:
        print(f"⚠️ Context retrieval failed: {e}")
        return ""


def answer_with_cag(question: str) -> tuple[str, str]:
    """
    Answer a question using CAG - retrieve context from vector store and generate answer.
    """
    print("🔄 Processing with CAG pipeline...")
    
    # Step 1: Retrieve relevant context for the question
    context = retrieve_context(question)
    print(f"🔍 Retrieved {len(context)} chars of context")
    
    # Step 2: Generate answer using retrieved context
    answer = answer_with_context(question, context)
    
    return context, answer


def process_with_cag(text: str, question: str) -> tuple[str, str]:
    """
    Process text using CAG pipeline and return both context and answer.
    DEPRECATED: Use answer_with_cag instead. This function stores text in vector store.
    """
    print("🔄 Processing with CAG pipeline (storing text)...")
    
    # Step 1: Chunk the text
    chunks = chunk_extracted_text(text)
    print(f"📄 Created {len(chunks)} chunks")
    
    # Step 2: Generate embeddings
    chunks_with_embeddings = generate_embeddings(chunks)
    valid_chunks = [c for c in chunks_with_embeddings if c["embedding"] is not None]
    print(f"🔢 Generated embeddings for {len(valid_chunks)} chunks")
    
    # Step 3: Store in Supabase
    if valid_chunks:
        count = upsert_to_supabase(valid_chunks)
        print(f"💾 Stored {count} chunks in Supabase")
    
    # Step 4: Retrieve relevant context for the question
    context = retrieve_context(question)
    print(f"🔍 Retrieved {len(context)} chars of context")
    
    # Step 5: Generate answer using retrieved context
    answer = answer_with_context(question, context)
    
    return context, answer


def answer_with_context(question: str, context: str) -> str:
    """
    Answer a question using retrieved context.
    """
    try:
        # Build prompt with context
        system_prompt = KB_PRIMARY_PROMPT

        user_prompt = f"""Context from knowledge base:
{context[:4000]}

Question: {question}

Instructions:
- First check if the answer is in the context
- If not, use your general knowledge to provide the best answer
- For multiple choice, select the most logical option

Answer:"""
        
        # Generate answer using reasoning model
        if REASON_MODEL.startswith("gpt"):
            # Use OpenAI for GPT models
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        else:
            # Use Ollama for other models
            result = ollama_chat(
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": 0.2},
            )
            
            if isinstance(result, dict):
                return result.get("message", {}).get("content", "Unable to generate answer.").strip()
            return result.message.content.strip()
        
    except Exception as e:
        print(f"⚠️ Answer generation failed: {e}")
        # Fallback to basic reasoning
        try:
            if REASON_MODEL.startswith("gpt"):
                client = OpenAI(api_key=OPENAI_API_KEY)
                response = client.chat.completions.create(
                    model=REASON_MODEL,
                    messages=[
                    {"role": "system", "content": KB_FALLBACK_PROMPT},
                    {"role": "user", "content": f"Question: {question}"},
                ],
                    temperature=0.2,
                )
                return response.choices[0].message.content.strip()
            else:
                result = ollama_chat(
                    model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": KB_FALLBACK_PROMPT},
                    {"role": "user", "content": f"Question: {question}"},
                ],
                )
                
                if isinstance(result, dict):
                    return result.get("message", {}).get("content", "Unable to generate answer.").strip()
                return result.message.content.strip()
        except Exception as e2:
            print(f"⚠️ Fallback reasoning also failed: {e2}")
            return f"Error: Unable to answer the question. ({e})"


# ---------------- RAG QUESTION ANSWERING ----------------
def answer_question_with_rag(question: str, kb_path: Path) -> str:
    """
    Answer a question using the reasoning model with knowledge base as context.
    """
    try:
        # Load knowledge base
        if kb_path.exists():
            kb_content = kb_path.read_text(encoding="utf-8")
            print(f"🔍 Using knowledge base ({len(kb_content)} chars) as context...")
        else:
            kb_content = ""
            print("⚠️ Knowledge base not found, answering without context...")
        
        # Build prompt with knowledge base context
        system_prompt = KB_PRIMARY_PROMPT

        user_prompt = f"""Context from knowledge base:
{kb_content[:4000]}

Question: {question}

Instructions:
- First check if the answer is in the context
- If not, use your general knowledge to provide the best answer
- For multiple choice, select the most logical option

Answer:"""
        
        # Generate answer using reasoning model
        if REASON_MODEL.startswith("gpt"):
            # Use OpenAI for GPT models
            client = OpenAI(api_key=OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        else:
            # Use Ollama for other models
            result = ollama_chat(
                model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                options={"temperature": 0.2},  # Slightly flexible but still accurate
            )
            
            if isinstance(result, dict):
                return result.get("message", {}).get("content", "Unable to generate answer.").strip()
            return result.message.content.strip()
        
    except Exception as e:
        print(f"⚠️ Answer generation failed: {e}")
        # Fallback to basic reasoning
        try:
            if REASON_MODEL.startswith("gpt"):
                # Use OpenAI for GPT models
                client = OpenAI(api_key=OPENAI_API_KEY)
                response = client.chat.completions.create(
                    model=REASON_MODEL,
                    messages=[
                    {"role": "system", "content": KB_FALLBACK_PROMPT},
                    {"role": "user", "content": f"Question: {question}"},
                ],
                    temperature=0.2,
                )
                return response.choices[0].message.content.strip()
            else:
                # Use Ollama for other models
                result = ollama_chat(
                    model=REASON_MODEL,
                messages=[
                    {"role": "system", "content": KB_FALLBACK_PROMPT},
                    {"role": "user", "content": f"Question: {question}"},
                ],
                )
                
                if isinstance(result, dict):
                    return result.get("message", {}).get("content", "Unable to generate answer.").strip()
                return result.message.content.strip()
        except Exception as e2:
            print(f"⚠️ Fallback reasoning also failed: {e2}")
            return f"Error: Unable to answer the question. ({e})"


# ---------------- INTERACTIVE LOOP ----------------
def main_loop(
    kb_filename: str = "knowledge_base.md",
    capture_region: dict = None,
    monitor_index: int = 1,
    use_cag: bool = True,
    extract_only: bool = False,
    top_offset: int | None = None,
    bottom_offset: int | None = None,
    left_offset: int | None = None,
    right_offset: int | None = None,
):
    if keyboard is None:
        raise RuntimeError(
            "Keyboard hotkeys require optional dependencies. "
            "Install with `pip install study-agents[vision]`."
        )

    print(f"📄 Knowledge Base Capture Agent active (CAG: {use_cag})...")
    print("Press 'Z' to capture screenshot and answer question. Press 'Esc' to exit.")

    seen = set()
    kb_path = Path(kb_filename)

    while True:
        if keyboard.is_pressed("esc"):
            print("\n🛑 KB capture agent stopped.")
            break

        if keyboard.is_pressed("z"):
            time.sleep(0.15)
            print("\n📸 Capturing screenshot...")
            img_path = capture_monitor(
                monitor_index,
                capture_region,
                top_offset=top_offset,
                bottom_offset=bottom_offset,
                left_offset=left_offset,
                right_offset=right_offset,
            )

            extracted_text = extract_text_with_docling(img_path)
            if not extracted_text:
                print("⚠️ No readable text found in screenshot.")
                time.sleep(0.5)
                continue

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

            print(f"\n📄 Extracted Question/Content:\n{markdown_text}\n")
            
            if extract_only:
                append_to_knowledge_base(f"{markdown_text}\n\n---\n", kb_path)
                print("📎 Saved Markdown (extract-only mode).")
                print("=" * 60)
                time.sleep(0.6)
                continue

            # Answer the question using CAG or traditional RAG
            print("🤔 Thinking...")
            
            if use_cag:
                # Use CAG pipeline - retrieve context from vector store
                context, answer = answer_with_cag(markdown_text)
                if context:
                    print(f"\n📚 Retrieved Context:\n{context[:1000]}{'...' if len(context) > 1000 else ''}\n")
            else:
                # Use traditional RAG
                answer = answer_question_with_rag(markdown_text, kb_path)
            
            print(f"\n💡 Answer:\n{answer}\n")
            print("=" * 60)

            # Also save to knowledge base for future reference (fallback)
            if not use_cag:
                append_to_knowledge_base(f"## Question\n{markdown_text}\n\n## Answer\n{answer}\n\n---\n", kb_path)

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
    parser.add_argument(
        "--region",
        "-r",
        nargs=4,
        type=int,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
        help="Capture region coordinates: x y width_height (e.g., --region 100 100 800 600)"
    )
    parser.add_argument(
        "--monitor",
        "-m",
        type=int,
        default=TARGET_MONITOR,
        help=f"Monitor index to capture (default: {TARGET_MONITOR})"
    )
    parser.add_argument(
        "--dpi",
        type=float,
        default=96.0,
        help="Monitor DPI used to convert inch margins to pixels (default: 96)."
    )
    parser.add_argument("--top-in", type=float, default=None, help="Top margin in inches.")
    parser.add_argument("--bottom-in", type=float, default=None, help="Bottom margin in inches.")
    parser.add_argument("--left-in", type=float, default=None, help="Left margin in inches.")
    parser.add_argument("--right-in", type=float, default=None, help="Right margin in inches.")
    parser.add_argument(
        "--cag",
        action="store_true",
        default=True,
        help="Use Context-Aware Grouping (CAG) with vector store (default: enabled)"
    )
    parser.add_argument(
        "--no-cag",
        action="store_false",
        dest="cag",
        help="Disable CAG and use traditional markdown file approach"
    )
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="Only capture/OCR/Markdown; skip answering entirely."
    )
    
    args = parser.parse_args()
    
    # Update global monitor index
    TARGET_MONITOR = args.monitor

    def _to_px(value: float | None) -> int | None:
        return int(value * args.dpi) if value is not None else None

    top_offset = _to_px(args.top_in)
    bottom_offset = _to_px(args.bottom_in)
    left_offset = _to_px(args.left_in)
    right_offset = _to_px(args.right_in)
    
    # Prepare capture region if specified
    capture_region = None
    if args.region:
        capture_region = {
            "x": args.region[0],
            "y": args.region[1], 
            "width": args.region[2],
            "height": args.region[3]
        }
        print(f"🎯 Custom capture region: x={capture_region['x']}, y={capture_region['y']}, w={capture_region['width']}, h={capture_region['height']}")
    
    output_path = Path(args.filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    main_loop(
        str(output_path),
        capture_region,
        args.monitor,
        args.cag,
        extract_only=args.extract_only,
        top_offset=top_offset,
        bottom_offset=bottom_offset,
        left_offset=left_offset,
        right_offset=right_offset,
    )
