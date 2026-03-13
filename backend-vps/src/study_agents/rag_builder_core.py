"""Reusable PDF→RAG bundle builder utilities.

These helpers are refactored from the standalone rag_builder script so we can
call them from other agents (CLI, MCP, etc.). No reasoning or Supabase logic is
added here—that will be layered on top in later steps.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import uuid
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _silent_run(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a callable while capturing stdout to keep MCP transport clean."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return func(*args, **kwargs)


def _use_docling_by_default() -> bool:
    # Prefer Docling unless explicitly disabled so extraction matches documented defaults.
    return os.getenv("RAG_USE_DOCLING", "true").lower() in {"1", "true", "yes", "on"}

try:  # PyMuPDF
    import fitz
except ImportError as exc:  # pragma: no cover - surfaced at runtime
    raise RuntimeError(
        "Missing dependency: pymupdf. Install with `pip install pymupdf`."
    ) from exc


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """File-system safe slug (A B.pdf -> A_B)."""

    cleaned = re.sub(r"[^\w\-. ]+", "", name, flags=re.UNICODE).strip()
    cleaned = cleaned.replace(" ", "_")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned or f"doc_{uuid.uuid4().hex[:8]}"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def quote_win(path: Path) -> str:
    """Windows-safe quoted path used in generated HOWTO commands."""

    return f'"{str(path)}"'


# ---------------------------------------------------------------------------
# PDF parsing + text utilities
# ---------------------------------------------------------------------------

def read_pdf_text_blocks(pdf_path: Path) -> list[dict]:
    """Read PDF text blocks using Docling with OCR capabilities (same as KB Capture Agent)."""
    if not _use_docling_by_default():
        return _extract_with_pymupdf(pdf_path)

    markdown_text = extract_pdf_with_docling(pdf_path)

    if not markdown_text:
        logger.warning("Docling failed, falling back to PyMuPDF...")
        return _extract_with_pymupdf(pdf_path)

    # Parse markdown to extract text by pages
    # Split by page markers if present, otherwise treat as single page
    pages = []

    # Try to detect page breaks in markdown
    page_breaks = []
    lines = markdown_text.split('\n')
    current_page = 1
    page_lines = []
    
    for line in lines:
        # Look for page break indicators
        if line.strip().startswith('--- Page ') or line.strip().startswith('\f'):
            if page_lines:
                page_text = '\n'.join(page_lines).strip()
                pages.append(
                    {
                        "page": current_page,
                        "text": page_text,
                        "is_toc": is_toc_page(page_text),
                    }
                )
                page_lines = []
                current_page += 1
        else:
            page_lines.append(line)
    
    # Add the last page
    if page_lines:
        page_text = '\n'.join(page_lines).strip()
        pages.append(
            {
                "page": current_page,
                "text": page_text,
                "is_toc": is_toc_page(page_text),
            }
        )
    
    # If no page breaks were detected, treat as single page
    if len(pages) == 0:
        text = markdown_text.strip()
        pages.append(
            {
                "page": 1,
                "text": text,
                "is_toc": is_toc_page(text),
            }
        )
    
    return pages
def extract_pdf_with_docling(pdf_path: Path) -> str:
    """
    Extracts text from PDF using Docling with OCR backend configuration.
    Same implementation as KB Capture Agent for consistency.
    """

    def _impl() -> str:
        if not _use_docling_by_default():
            logger.info("RAG_USE_DOCLING disabled; using PyMuPDF extraction.")
            return ""

        try:
            from docling.document_converter import DocumentConverter
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.models.easyocr_model import EasyOcrOptions
            from docling.models.rapid_ocr_model import RapidOcrOptions
            from docling.models.tesseract_ocr_model import TesseractOcrOptions
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "Docling OCR support requires optional dependencies. "
                "Install with `pip install study-agents[docling]`."
            ) from exc
        text = ""

        def _convert(pipeline_options):
            converter = _silent_run(DocumentConverter)
            return _silent_run(converter.convert, str(pdf_path))

        # Try EasyOCR backend first (best for English)
        try:
            logger.info("Configuring Docling with EasyOCR backend...")
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True

            ocr_options = EasyOcrOptions(force_full_page_ocr=True)
            ocr_options.lang = ["en"]
            pipeline_options.ocr_options = ocr_options

            result = _convert(pipeline_options)

            if hasattr(result, "document"):
                doc = result.document
                text = doc.export_to_markdown()
                if text == "<!-- image -->":
                    text = ""
                elif text.strip():
                    text = text.replace('<!-- image -->', '').strip()

            if text:
                logger.info("Docling with EasyOCR backend successful")
        except Exception as e:
            logger.warning("Docling EasyOCR backend failed: %s", e)

        if not text:
            try:
                logger.info("Trying Docling with Tesseract backend...")
                pipeline_options = PdfPipelineOptions()
                pipeline_options.do_ocr = True
                pipeline_options.do_table_structure = True
                pipeline_options.table_structure_options.do_cell_matching = True

                ocr_options = TesseractOcrOptions(force_full_page_ocr=True)
                pipeline_options.ocr_options = ocr_options

                result = _convert(pipeline_options)

                if hasattr(result, "document"):
                    doc = result.document
                    text = doc.export_to_markdown()
                    if text == "<!-- image -->":
                        text = ""
                    elif text.strip():
                        text = text.replace('<!-- image -->', '').strip()

                if text:
                    logger.info("Docling with Tesseract backend successful")
            except Exception as e:
                logger.warning("Docling Tesseract backend failed: %s", e)

        if not text:
            try:
                logger.info("Trying Docling with RapidOCR backend...")
                pipeline_options = PdfPipelineOptions()
                pipeline_options.do_ocr = True
                pipeline_options.do_table_structure = True
                pipeline_options.table_structure_options.do_cell_matching = True

                ocr_options = RapidOcrOptions(force_full_page_ocr=True)
                pipeline_options.ocr_options = ocr_options

                result = _convert(pipeline_options)

                if hasattr(result, "document"):
                    doc = result.document
                    text = doc.export_to_markdown()
                    if text == "<!-- image -->":
                        text = ""
                    elif text.strip():
                        text = text.replace('<!-- image -->', '').strip()

                if text:
                    logger.info("Docling with RapidOCR backend successful")
            except Exception as e:
                logger.warning("Docling RapidOCR backend failed: %s", e)

        if not text:
            logger.warning("All Docling backends failed. Trying direct EasyOCR...")
            try:
                from easyocr import Reader

                reader = Reader(['en'], gpu=False)
                result = _silent_run(reader.readtext, str(pdf_path))

                texts = []
                for (bbox, extracted_text, confidence) in result:
                    if confidence > 0.5:
                        texts.append(extracted_text)

                if texts:
                    text = "\n".join(texts)
                    logger.info("Direct EasyOCR successful")
            except Exception as e:
                logger.warning("Direct EasyOCR failed: %s", e)

        return text

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return _impl()


def _extract_with_pymupdf(pdf_path: Path) -> list[dict]:
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text() or ""
        pages.append({"page": i + 1, "text": text, "is_toc": is_toc_page(text)})
    doc.close()
    return pages


def is_toc_page(text: str) -> bool:
    """
    Heuristic detector for table-of-contents style pages.

    We treat TOC pages as useful for structure (headings) but avoid
    indexing them as regular retrieval content in the vector store.
    """
    lower = text.lower()
    if "table of contents" not in lower and "contents" not in lower:
        return False

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False

    dot_leader_lines = 0
    page_number_lines = 0
    prose_lines = 0

    for ln in lines:
        # e.g. "Chapter 1 .......... 12"
        if re.search(r"\.+\s*\d+$", ln):
            dot_leader_lines += 1
        # Short line ending with a number (likely "Heading 12")
        elif re.search(r"\s\d+$", ln) and len(ln.split()) <= 8:
            page_number_lines += 1
        # Longer prose-like lines
        elif len(ln.split()) > 8 and not ln.endswith("..."):
            prose_lines += 1

    total = len(lines)
    if total == 0:
        return False

    toc_ratio = (dot_leader_lines + page_number_lines) / total
    prose_ratio = prose_lines / total

    # Mostly dot-leader / page-number lines, very little prose.
    return toc_ratio > 0.4 and prose_ratio < 0.2


def split_into_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text.strip(), flags=re.UNICODE)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        parts = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return parts


def guess_headings(paragraphs: Sequence[str]) -> list[str]:
    heads: list[str] = []
    for para in paragraphs:
        if (
            len(para) <= 120
            and (para.istitle() or para.isupper())
            and not para.endswith(".")
            and len(para.split()) <= 10
        ):
            heads.append(para)
    return heads


def chunk_text(
    paragraphs: Iterable[str], chunk_size: int = 1200, overlap: int = 150
) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        plen = len(para)
        if current and current_len + plen + 1 > chunk_size:
            chunk = "\n\n".join(current)
            chunks.append(chunk)
            if overlap > 0 and chunk:
                tail = chunk[-overlap:]
                current = [tail]
                current_len = len(tail)
            else:
                current, current_len = [], 0

        current.append(para)
        current_len += plen + 2

    if current:
        chunks.append("\n\n".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Graph structures
# ---------------------------------------------------------------------------


@dataclass
class Node:
    id: str
    type: str
    title: str
    attrs: dict


@dataclass
class Edge:
    src: str
    rel: str
    dst: str
    attrs: dict


# ---------------------------------------------------------------------------
# HOWTO builder
# ---------------------------------------------------------------------------

def build_howto_md(
    *,
    doc_title_disp: str,
    ts: str,
    doc_folder: Path,
    base_slug: str,
    chunks_fp: Path,
    nodes_fp: Path,
    edges_fp: Path,
    triples_fp: Path,
    analysis_fp: Path,
) -> str:
    lines: list[str] = []
    lines += [f"# HOWTO for **{doc_title_disp}** (generated {ts})", ""]
    lines += ["All files for this document:", "```", str(doc_folder), "```", ""]
    lines += [
        "## 1) Prereqs",
        "- `.env` with: `SUPABASE_URL`, `SUPABASE_KEY`, `OPENAI_API_KEY`",
        "- `pip install supabase openai python-dotenv`",
        "",
    ]

    # Supabase DDL
    lines += [
        "## 2) Supabase SQL",
        "```sql",
        "create extension if not exists vector;",
        "",
        "create table if not exists documents (",
        "  id        text primary key,",
        "  content   text not null,",
        "  embedding vector(1536),",
        "  meta      jsonb",
        ");",
        "create index if not exists documents_embedding_idx",
        "  on documents using ivfflat (embedding vector_cosine_ops) with (lists = 100);",
        "",
        "create or replace function match_documents(",
        "  query_embedding vector(1536),",
        "  match_threshold double precision default 0.2,",
        "  match_count int default 8",
        ") returns table (id text, content text, similarity double precision)",
        "language sql stable as $$",
        "  select d.id, d.content,",
        "         1 - (d.embedding <=> query_embedding) as similarity",
        "  from documents d",
        "  where d.embedding is not null",
        "    and 1 - (d.embedding <=> query_embedding) >= match_threshold",
        "  order by d.embedding <=> query_embedding",
        "  limit match_count",
        "$$;",
        "",
        "create table if not exists kg_nodes (",
        "  id    text primary key,",
        "  type  text,",
        "  title text,",
        "  attrs jsonb",
        ");",
        "create table if not exists kg_edges (",
        "  id    bigserial primary key,",
        "  src   text not null references kg_nodes(id) on delete cascade,",
        "  rel   text not null,",
        "  dst   text not null references kg_nodes(id) on delete cascade,",
        "  attrs jsonb",
        ");",
        "create index if not exists kg_nodes_type_idx on kg_nodes(type);",
        "create index if not exists kg_edges_src_idx  on kg_edges(src);",
        "create index if not exists kg_edges_dst_idx  on kg_edges(dst);",
        "create index if not exists kg_edges_rel_idx  on kg_edges(rel);",
        "```",
        "",
    ]

    # Upsert commands
    lines += [
        "## 3) Upsert vectors (chunks → documents)",
        "```cmd",
        (
            "python -c \"import os,json;from dotenv import load_dotenv;load_dotenv();"
            "from supabase import create_client;from openai import OpenAI;"
            "sb=create_client(os.getenv('SUPABASE_URL'),os.getenv('SUPABASE_KEY'));"
            "cli=OpenAI();f=r'{chunks_fp}';"
            "[sb.table('documents').upsert({'id':o['id'],'content':o['text'],"
            "'meta':{'section_id':o.get('section_id'),'page_start':o.get('page_start'),"
            "'page_end':o.get('page_end'),'tags':o.get('tags')},"
            "'embedding':cli.embeddings.create(model='text-embedding-3-small',"
            "input=o['text']).data[0].embedding}).execute()"
            " for o in map(json.loads,open(f,encoding='utf-8').read().splitlines())]\""
        ),
        "```",
        "",
    ]

    lines += [
        "## 4) Upsert KG (nodes + edges)",
        "```cmd",
        (
            "python -c \"import os,json;from dotenv import load_dotenv;load_dotenv();"
            "from supabase import create_client;"
            "sb=create_client(os.getenv('SUPABASE_URL'),os.getenv('SUPABASE_KEY'));"
            "f=r'{nodes_fp}';"
            "[sb.table('kg_nodes').upsert({'id':o['id'],'type':o.get('type'),"
            "'title':o.get('title'),'attrs':o.get('attrs')}).execute()"
            " for o in map(json.loads,open(f,encoding='utf-8').read().splitlines())]\""
        ),
        "```",
        "```cmd",
        (
            "python -c \"import os,json;from dotenv import load_dotenv;load_dotenv();"
            "from supabase import create_client;"
            "sb=create_client(os.getenv('SUPABASE_URL'),os.getenv('SUPABASE_KEY'));"
            "f=r'{edges_fp}';"
            "[sb.table('kg_edges').insert({'src':o['src'],'rel':o['rel'],'dst':o['dst'],"
            "'attrs':o.get('attrs')}).execute()"
            " for o in map(json.loads,open(f,encoding='utf-8').read().splitlines())]\""
        ),
        "```",
        "",
    ]

    lines += [
        "## 5) Quick RAG test",
        "```cmd",
        (
            "python -c \"import os,textwrap,json;from dotenv import load_dotenv;load_dotenv();"
            "from supabase import create_client;from openai import OpenAI;"
            "sb=create_client(os.getenv('SUPABASE_URL'),os.getenv('SUPABASE_KEY'));"
            "cli=OpenAI();q='Summarize the most important guidance for initial contact after a loss.';"
            "emb=cli.embeddings.create(model='text-embedding-3-small',input=q).data[0].embedding;"
            "hits=sb.rpc('match_documents',{'query_embedding':emb,'match_threshold':0.15,'match_count':8})."
            "execute().data or [];ctx='\\n\\n---\\n'.join([h['content'] for h in hits]);"
            "msg=f\"Answer as a subject-matter expert using ONLY CONTEXT. If missing, say so.\\n\\n"
            "QUESTION:\\n{q}\\n\\nCONTEXT:\\n{ctx[:12000]}\";"
            "ans=cli.chat.completions.create(model='gpt-4o-mini',messages=[{'role':'system','content':'You answer strictly from provided context.'},{'role':'user','content':msg}],temperature=0);"
            "print(ans.choices[0].message.content)\""
        ),
        "```",
        "",
    ]

    lines += [
        "## 6) Sample KG queries",
        "```sql",
        "select id,title from kg_nodes where type='Section' order by title;",
        "```",
        "```sql",
        "select e.rel, e.dst, n.title",
        "from kg_edges e",
        "left join kg_nodes n on n.id=e.dst",
        f"where e.src like 'SEC:{base_slug}:%'",
        "limit 50;",
        "```",
        "",
    ]

    lines += [
        "**Files**",
        f"- {chunks_fp.name}",
        f"- {nodes_fp.name}",
        f"- {edges_fp.name}",
        f"- {triples_fp.name}",
        f"- {analysis_fp.name}",
        f"- {base_slug}.HOWTO.md",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto parameter suggestion
# ---------------------------------------------------------------------------

def suggest_params(pdf_path: Path) -> dict:
    pages = read_pdf_text_blocks(pdf_path)
    page_texts = [p["text"] for p in pages]
    total_chars = sum(len(t) for t in page_texts)
    n_pages = max(1, len(page_texts))
    avg_chars_page = total_chars / n_pages

    all_text = "\n\n".join(page_texts)
    paras = split_into_paragraphs(all_text)
    n_paras = max(1, len(paras))
    avg_chars_para = sum(len(p) for p in paras) / n_paras

    heads = guess_headings(paras)
    n_heads = len(heads)

    density = avg_chars_page
    if density > 8000:
        chunk_size = 1600
    elif density > 5000:
        chunk_size = 1400
    elif density > 2500:
        chunk_size = 1200
    else:
        chunk_size = 1000

    overlap = max(120, min(240, int(chunk_size * 0.18)))

    if n_heads >= 30:
        max_sections = min(120, int(n_heads * 1.2))
    elif n_heads >= 10:
        max_sections = min(100, int(n_heads * 1.5))
    else:
        max_sections = min(80, max(40, int(n_pages * 1.2)))

    triples = int(min(1200, max(200, n_paras * 0.35)))

    return {
        "chunk_size": chunk_size,
        "overlap": overlap,
        "max_sections": max_sections,
        "triples": triples,
        "stats": {
            "pages": n_pages,
            "total_chars": total_chars,
            "avg_chars_page": int(avg_chars_page),
            "avg_chars_para": int(avg_chars_para),
            "headings_detected": n_heads,
            "paras": n_paras,
        },
    }


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------

def _split_into_sections(
    paragraphs: Sequence[str],
    headings: Sequence[str],
    *,
    doc_title: str,
) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    if len(headings) >= 3:
        current_title: str | None = None
        current_buf: list[str] = []
        heading_set = set(headings)
        for para in paragraphs:
            if para in heading_set:
                if current_title or current_buf:
                    sections.append(
                        (current_title or "Section", "\n\n".join(current_buf).strip())
                    )
                current_title = para
                current_buf = []
            else:
                current_buf.append(para)
        if current_title or current_buf:
            sections.append((current_title or "Section", "\n\n".join(current_buf).strip()))
    else:
        sections = [(doc_title, "\n\n".join(paragraphs))]
    return sections


def build_from_pdf(
    *,
    pdf_path: Path,
    outdir: Path,
    chunk_size: int = 1200,
    chunk_overlap: int = 150,
    max_sections: int = 80,
    triples_budget: int = 300,
) -> dict:
    doc_title = pdf_path.stem
    base_slug = slugify(doc_title)
    doc_folder = ensure_dir(outdir / base_slug)

    pages = read_pdf_text_blocks(pdf_path)

    # Separate TOC-like pages from body pages. TOC pages will still be
    # used for structural headings, but will not be chunked/embedded.
    toc_pages = [p for p in pages if p.get("is_toc")]
    body_pages = [p for p in pages if not p.get("is_toc")]
    if not body_pages:
        body_pages = pages

    all_text = "\n\n".join(p["text"] for p in body_pages).strip()
    paragraphs = split_into_paragraphs(all_text)

    if toc_pages:
        toc_text = "\n\n".join(p["text"] for p in toc_pages)
        toc_paras = split_into_paragraphs(toc_text)
        toc_headings = guess_headings(toc_paras)
        headings = toc_headings or guess_headings(paragraphs)
    else:
        headings = guess_headings(paragraphs)
    sections = _split_into_sections(paragraphs, headings, doc_title=doc_title)
    if max_sections and len(sections) > max_sections:
        sections = sections[:max_sections]

    chunks_out: list[dict] = []
    nodes_out: list[Node] = []
    edges_out: list[Edge] = []
    triples_out: list[dict] = []

    analysis = {
        "input_pdf": str(pdf_path),
        "created": now_iso(),
        "section_count": len(sections),
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "max_sections": max_sections,
        "triples_budget": triples_budget,
        "notes": "Auto/Manual params applied.",
    }

    doc_node_id = f"DOC:{base_slug}"
    nodes_out.append(
        Node(id=doc_node_id, type="Document", title=doc_title, attrs={"path": str(pdf_path)})
    )

    remaining_triples = triples_budget
    for si, (title, body) in enumerate(sections, start=1):
        sec_id = f"SEC:{base_slug}:{si:03d}"
        nodes_out.append(Node(id=sec_id, type="Section", title=title, attrs={"order": si}))
        edges_out.append(Edge(src=doc_node_id, rel="contains", dst=sec_id, attrs={}))

        sec_paras = split_into_paragraphs(body)
        sec_chunks = chunk_text(sec_paras, chunk_size=chunk_size, overlap=chunk_overlap)
        for cj, text in enumerate(sec_chunks, start=1):
            chunks_out.append(
                {
                    "id": f"CHUNK:{base_slug}:{si:03d}:{cj:03d}",
                    "text": text,
                    "section_id": sec_id,
                    "section_title": title,
                    "chunk_index": cj,
                    "page_start": None,
                    "page_end": None,
                    "tags": [base_slug, "pdf"],
                }
            )

        if remaining_triples > 0 and body:
            lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
            for ln in lines:
                if remaining_triples <= 0:
                    break
                match = re.match(r"^([A-Za-z][\w\s\-/&]+)\s*[:\-–]\s*(.+)$", ln)
                if match:
                    key = match.group(1).strip()
                    val = match.group(2).strip()
                    subj_id = f"ENT:{base_slug}:{uuid.uuid4().hex[:8]}"
                    obj_id = f"ENT:{base_slug}:{uuid.uuid4().hex[:8]}"
                    nodes_out.append(Node(id=subj_id, type="Entity", title=key, attrs={}))
                    nodes_out.append(Node(id=obj_id, type="Entity", title=val[:80], attrs={}))
                    edges_out.append(
                        Edge(src=subj_id, rel="defines", dst=obj_id, attrs={"section": sec_id})
                    )
                    triples_out.append(
                        {"s": subj_id, "p": "defines", "o": obj_id, "section": sec_id, "text": ln}
                    )
                    remaining_triples -= 1

    nodes_out = list({node.id: node for node in nodes_out}.values())

    chunks_fp = doc_folder / f"{base_slug}.chunks.jsonl"
    nodes_fp = doc_folder / f"{base_slug}.nodes.jsonl"
    edges_fp = doc_folder / f"{base_slug}.edges.jsonl"
    triples_fp = doc_folder / f"{base_slug}.triples.jsonl"
    analysis_fp = doc_folder / f"{base_slug}.analysis.json"
    howto_fp = doc_folder / f"{base_slug}.HOWTO.md"
    markdown_fp = doc_folder / f"{base_slug}.CAG.md"

    with open(chunks_fp, "w", encoding="utf-8") as f:
        for chunk in chunks_out:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    with open(nodes_fp, "w", encoding="utf-8") as f:
        for node in nodes_out:
            f.write(
                json.dumps(
                    {"id": node.id, "type": node.type, "title": node.title, "attrs": node.attrs},
                    ensure_ascii=False,
                )
                + "\n"
            )

    with open(edges_fp, "w", encoding="utf-8") as f:
        for edge in edges_out:
            f.write(
                json.dumps({"src": edge.src, "rel": edge.rel, "dst": edge.dst, "attrs": edge.attrs}, ensure_ascii=False)
                + "\n"
            )

    with open(triples_fp, "w", encoding="utf-8") as f:
        for triple in triples_out:
            f.write(json.dumps(triple, ensure_ascii=False) + "\n")

    with open(analysis_fp, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    howto_text = build_howto_md(
        doc_title_disp=doc_title,
        ts=now_iso(),
        doc_folder=doc_folder,
        base_slug=base_slug,
        chunks_fp=chunks_fp,
        nodes_fp=nodes_fp,
        edges_fp=edges_fp,
        triples_fp=triples_fp,
        analysis_fp=analysis_fp,
    )
    with open(howto_fp, "w", encoding="utf-8") as f:
        f.write(howto_text)

    _write_markdown_summary(
        markdown_fp,
        doc_title=doc_title,
        sections=sections,
        chunks=chunks_out,
        triples=triples_out,
    )

    return {
        "folder": str(doc_folder),
        "chunks": str(chunks_fp),
        "nodes": str(nodes_fp),
        "edges": str(edges_fp),
        "triples": str(triples_fp),
        "analysis": str(analysis_fp),
        "howto": str(howto_fp),
        "markdown": str(markdown_fp),
    }


def _write_markdown_summary(
    markdown_path: Path,
    *,
    doc_title: str,
    sections: list[tuple[str, str]],
    chunks: list[dict],
    triples: list[dict],
) -> None:
    lines: list[str] = []
    lines.append(f"# {doc_title}")
    lines.append(f"_CAG summary generated {now_iso()}_")
    lines.append("")
    lines.append("## Overview")
    lines.append(f"- Sections: {len(sections)}")
    lines.append(f"- Chunks: {len(chunks)}")
    lines.append(f"- Triples captured: {len(triples)}")
    lines.append("")

    chunks_by_section: dict[str, list[dict]] = {}
    for chunk in chunks:
        chunks_by_section.setdefault(chunk["section_id"], []).append(chunk)

    for idx, (title, _) in enumerate(sections, start=1):
        sec_id = f"SEC:{slugify(doc_title)}:{idx:03d}"
        lines.append(f"## Section {idx}: {title}")
        section_chunks = chunks_by_section.get(sec_id, [])
        lines.append(f"Chunks in this section: {len(section_chunks)}")
        lines.append("")
        for chunk in sorted(section_chunks, key=lambda c: c.get("chunk_index", 0)):
            lines.append(f"### Chunk {chunk.get('chunk_index', '?')} ({chunk['id']})")
            tags = ", ".join(chunk.get("tags") or [])
            if tags:
                lines.append(f"_Tags: {tags}_")
            lines.append("```")
            lines.append(chunk["text"])
            lines.append("```")
            lines.append("")

    if triples:
        lines.append("## Knowledge Triples")
        for triple in triples:
            lines.append(
                f"- `{triple['s']}` --{triple['p']}--> `{triple['o']}` (section {triple.get('section')})"
            )
        lines.append("")

    markdown_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


__all__ = [
    "slugify",
    "now_iso",
    "ensure_dir",
    "quote_win",
    "read_pdf_text_blocks",
    "split_into_paragraphs",
    "guess_headings",
    "chunk_text",
    "Node",
    "Edge",
    "build_howto_md",
    "suggest_params",
    "build_from_pdf",
]
