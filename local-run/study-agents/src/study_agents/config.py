"""Backwards-compatible config constants sourced from central settings."""
from __future__ import annotations

from .settings import get_settings

_settings = get_settings()

OPENAI_API_KEY = _settings.openai_api_key
OPENAI_EMBED_MODEL = _settings.openai_embed_model

SUPABASE_URL = _settings.supabase_url
SUPABASE_KEY = _settings.supabase_key
SUPABASE_DOCS_TABLE = _settings.supabase_docs_table
SUPABASE_NODES_TABLE = _settings.supabase_nodes_table
SUPABASE_EDGES_TABLE = _settings.supabase_edges_table

OLLAMA_API_KEY = _settings.ollama_api_key
OLLAMA_HOST = _settings.ollama_host
REASON_MODEL = _settings.reason_model

TARGET_MONITOR = _settings.target_monitor
CAPTURE_INTERVAL = _settings.capture_interval
SCREENSHOT_DIR = _settings.screenshot_dir

SCHEMA_PDF_PATH = _settings.schema_pdf_path
SCHEMA_DEFAULT_CHUNK_SIZE = _settings.schema_default_chunk_size
SCHEMA_DEFAULT_OVERLAP = _settings.schema_default_overlap
MODEL_PROVIDER = _settings.schema_model_provider
MODEL_NAME = _settings.schema_model_name

USE_HYBRID_RETRIEVAL = _settings.use_hybrid_retrieval
