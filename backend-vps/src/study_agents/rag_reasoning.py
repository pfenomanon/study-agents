from __future__ import annotations

"""Reasoning-driven planner + orchestration for PDF → RAG bundles.

Step 2: builds on rag_builder_core by adding a reasoning model that chooses chunking
and knowledge-graph parameters, then runs the bundle builder with those settings.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

from openai import OpenAI

from .config import OLLAMA_API_KEY, OLLAMA_HOST, OPENAI_API_KEY, REASON_MODEL
from .rag_builder_core import (
    build_from_pdf,
    guess_headings,
    read_pdf_text_blocks,
    split_into_paragraphs,
    suggest_params,
)

# ---------------------------------------------------------------------------
# Shared CAG prompt
# ---------------------------------------------------------------------------


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAG_PROMPT_PATH = PROJECT_ROOT / "CAG_CHUNKING_STRATEGY.md"


def _load_cag_prompt() -> str:
    try:
        return CAG_PROMPT_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return (
            "You are a Context Augmented Generation (CAG) architect. Design chunking and "
            "metadata strategies that maximize retrieval precision, grounding, reranking, "
            "and downstream flexibility. Always return JSON."
        )


CAG_STRATEGY_PROMPT = _load_cag_prompt()

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ReasoningPlan:
    chunk_size: int
    overlap: int
    max_sections: int
    triples: int
    provider: str
    model: str
    notes: str
    stats: dict
    raw_response: str


@dataclass
class BuildResult:
    plan: ReasoningPlan
    artifacts: dict


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class RAGReasoningPlanner:
    """Decides chunk/graph parameters via LLM reasoning with guardrails."""

    def __init__(
        self,
        *,
        default_model: str | None = None,
        provider: Literal["auto", "openai", "ollama"] = "auto",
    ) -> None:
        self._provider = provider
        self._default_model = default_model or REASON_MODEL
        self._openai_client: Optional[OpenAI] = None

        if OPENAI_API_KEY:
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)

        # configure Ollama host/key if provided
        if OLLAMA_HOST:
            import os
            os.environ["OLLAMA_HOST"] = OLLAMA_HOST
        if OLLAMA_API_KEY:
            import os
            os.environ["OLLAMA_API_KEY"] = OLLAMA_API_KEY

    # Public -----------------------------------------------------------------

    def plan_for_pdf(
        self,
        pdf_path: Path,
        *,
        overrides: Optional[dict] = None,
    ) -> ReasoningPlan:
        stats = self._collect_stats(pdf_path)
        suggestion = suggest_params(pdf_path)
        provider = self._resolve_provider()

        if provider is None:  # no reasoning available
            return self._fallback_plan(suggestion, provider="fallback", raw="")

        prompt = self._build_prompt(stats, suggestion)
        response_text = self._call_reasoning_model(prompt, provider)
        plan_dict = self._safe_parse_plan(response_text)

        if not plan_dict:
            return self._fallback_plan(suggestion, provider="fallback", raw=response_text)

        merged = self._merge_plan(suggestion, plan_dict, overrides)
        return ReasoningPlan(
            chunk_size=merged["chunk_size"],
            overlap=merged["overlap"],
            max_sections=merged["max_sections"],
            triples=merged["triples"],
            provider=provider,
            model=self._default_model,
            notes=merged.get("notes", ""),
            stats=stats,
            raw_response=response_text,
        )

    # Internal helpers --------------------------------------------------------

    def _collect_stats(self, pdf_path: Path) -> dict:
        pages = read_pdf_text_blocks(pdf_path)
        page_texts = [page["text"] for page in pages]
        all_text = "\n\n".join(page_texts)
        paragraphs = split_into_paragraphs(all_text)
        heads = guess_headings(paragraphs)
        stats = {
            "pages": len(pages),
            "avg_chars_page": int(sum(len(t) for t in page_texts) / max(1, len(pages))),
            "paragraphs": len(paragraphs),
            "headings_detected": len(heads),
        }
        stats["sample_paragraphs"] = paragraphs[:5]
        return stats

    def _resolve_provider(self) -> Optional[str]:
        if self._provider == "openai":
            return "openai" if self._openai_client else None
        if self._provider == "ollama":
            return "ollama"
        # auto preference: OpenAI → Ollama → fallback
        if self._openai_client and self._default_model_is_openai():
            return "openai"
        return "ollama" if OLLAMA_HOST else None

    def _default_model_is_openai(self) -> bool:
        # Heuristic: OpenAI models rarely contain ':'; Ollama models often do (e.g., deepseek:latest)
        return ":" not in self._default_model.lower()

    def _build_prompt(self, stats: dict, suggestion: dict) -> str:
        sample = "\n\n".join(stats.get("sample_paragraphs", [])[:3])
        prompt = (
            "You are a senior Retrieval-Augmented Generation architect.\n"
            "Given PDF statistics and heuristic defaults, propose chunking + knowledge graph\n"
            "settings optimized for high-quality RAG + Context-Aware Grouping (CAG).\n\n"
            f"PDF_STATS = {json.dumps({k: v for k, v in stats.items() if k != 'sample_paragraphs'})}\n"
            f"DEFAULTS = {json.dumps({k: suggestion[k] for k in ('chunk_size','overlap','max_sections','triples')})}\n"
            f"TEXT_SAMPLE = \"{sample[:2000]}\"\n\n"
            "Return JSON with keys:\n"
            "  chunk_size (int, 800-2000)\n"
            "  overlap (int, 80-300)\n"
            "  max_sections (int)\n"
            "  triples (int)\n"
            "  notes (string rationale <= 200 chars)\n"
            "Values must be integers, practical, and consistent with the defaults/stats."
        )
        return prompt

    def _call_reasoning_model(self, prompt: str, provider: str) -> str:
        if provider == "openai" and self._openai_client:
            response = self._openai_client.chat.completions.create(
                model=self._default_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": CAG_STRATEGY_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content or ""

        # default to Ollama
        result = ollama_chat(
            model=self._default_model,
            messages=[
                {"role": "system", "content": CAG_STRATEGY_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        content = result.get("message", {}).get("content")
        if content:
            return content
        if hasattr(result, "message"):
            return result.message.get("content", "")  # type: ignore[attr-defined]
        return ""

    def _safe_parse_plan(self, response_text: str) -> Optional[dict]:
        if not response_text:
            return None
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        snippet = response_text[start : end + 1]
        try:
            data = json.loads(snippet)
        except json.JSONDecodeError:
            return None
        required = {"chunk_size", "overlap", "max_sections", "triples"}
        if not required.issubset(data):
            return None
        return data

    def _merge_plan(self, suggestion: dict, plan_dict: dict, overrides: Optional[dict]) -> dict:
        values = {**suggestion}
        values.update({k: int(plan_dict.get(k, values[k])) for k in ("chunk_size", "overlap", "max_sections", "triples")})
        if overrides:
            for key in ("chunk_size", "overlap", "max_sections", "triples"):
                if overrides.get(key) is not None:
                    values[key] = int(overrides[key])
        values["notes"] = str(plan_dict.get("notes", ""))
        return values

    def _fallback_plan(self, suggestion: dict, provider: str, raw: str) -> ReasoningPlan:
        return ReasoningPlan(
            chunk_size=suggestion["chunk_size"],
            overlap=suggestion["overlap"],
            max_sections=suggestion["max_sections"],
            triples=suggestion["triples"],
            provider=provider,
            model=self._default_model,
            notes="heuristic defaults",
            stats=suggestion.get("stats", {}),
            raw_response=raw,
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class RAGBuildAgent:
    def __init__(self, planner: Optional[RAGReasoningPlanner] = None) -> None:
        self.planner = planner or RAGReasoningPlanner()

    def build_bundle(
        self,
        pdf_path: Path,
        *,
        outdir: Path,
        overrides: Optional[dict] = None,
    ) -> BuildResult:
        plan = self.planner.plan_for_pdf(pdf_path, overrides=overrides)
        artifacts = build_from_pdf(
            pdf_path=pdf_path,
            outdir=outdir,
            chunk_size=plan.chunk_size,
            chunk_overlap=plan.overlap,
            max_sections=plan.max_sections,
            triples_budget=plan.triples,
        )
        # attach plan metadata file next to artifacts
        plan_path = Path(artifacts["folder"]) / "reasoning_plan.json"
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump({"plan": asdict(plan)}, f, indent=2, ensure_ascii=False)
        artifacts["plan"] = str(plan_path)
        return BuildResult(plan=plan, artifacts=artifacts)


__all__ = ["RAGReasoningPlanner", "RAGBuildAgent", "ReasoningPlan", "BuildResult"]
from .ollama_client import chat as ollama_chat
