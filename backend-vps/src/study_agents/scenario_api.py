from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .cag_agent import CAGAgent
from .kg_pipeline import (
    EpisodeChunk,
    EpisodePayload,
    IngestionConfig,
    KnowledgeIngestionService,
)
from .rag_builder_core import chunk_text, slugify, split_into_paragraphs
from .settings import get_settings

SCENARIO_STORAGE_DIR = Path(
    os.getenv("SCENARIO_STORAGE_DIR", "data/scenarios")
).expanduser()
SCENARIO_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

INGEST_CHUNK_SIZE = int(os.getenv("SCENARIO_INGEST_CHUNK_SIZE", "900"))
INGEST_OVERLAP = int(os.getenv("SCENARIO_INGEST_OVERLAP", "120"))


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class TerminologyOverrides(BaseModel):
    terms: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of generic terms to carrier-specific replacements.",
    )


class CarrierProfile(BaseModel):
    type: Literal["generic", "carrier"] = "generic"
    name: Optional[str] = Field(
        default=None, description="Carrier or IA firm name, if applicable."
    )
    playbook: Optional[str] = Field(
        default=None, description="Internal playbook or workflow identifier."
    )
    terminology: Optional[TerminologyOverrides] = None


class CoverageItem(BaseModel):
    name: str
    limit: Optional[str] = None
    deductible: Optional[str] = None
    notes: Optional[str] = None


class EvidenceItem(BaseModel):
    title: str
    description: Optional[str] = None
    content: Optional[str] = None
    file_reference: Optional[str] = Field(
        default=None, description="Path or URL to source material."
    )


class TaskItem(BaseModel):
    task: str
    status: Literal["pending", "in_progress", "completed"] = "pending"
    owner: Optional[str] = None
    notes: Optional[str] = None


class ScenarioPayload(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier.")
    policy_type: Literal["homeowners", "dwelling", "auto", "commercial", "other"] = "homeowners"
    peril: str
    loss_date: Optional[str] = None
    loss_summary: str
    coverage_profile: List[CoverageItem] = Field(default_factory=list)
    evidence_items: List[EvidenceItem] = Field(default_factory=list)
    open_tasks: List[TaskItem] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    carrier_profile: CarrierProfile = Field(
        default_factory=CarrierProfile,
        description="Indicates whether the scenario uses a carrier-specific playbook or the generic one.",
    )
    freeform_notes: Optional[str] = None


class ScenarioResponse(BaseModel):
    scenario: ScenarioPayload
    ingested_at: Optional[str] = None
    ingestion_summary: Optional[Dict[str, int]] = None


class ScenarioQuestion(BaseModel):
    question: str = Field(..., min_length=4)


class Citation(BaseModel):
    source: str
    details: Optional[str] = None


class DocumentationChecklistItem(BaseModel):
    item: str
    status: Literal["pending", "received", "not_applicable"] = "pending"
    notes: Optional[str] = None


class StructuredAdjusterAnswer(BaseModel):
    scenario_id: str
    question: str
    summary: str
    recommended_steps: List[str]
    coverage_analysis: Dict[str, str]
    documentation_checklist: List[DocumentationChecklistItem]
    citations: List[Citation]
    raw_answer: str


# --------------------------------------------------------------------------- #
# Scenario repository
# --------------------------------------------------------------------------- #


class ScenarioRepository:
    def __init__(self, storage_dir: Path):
        self.storage_dir = storage_dir

    def _path(self, scenario_id: str) -> Path:
        slug = slugify(f"scenario_{scenario_id}")
        return self.storage_dir / f"{slug}.json"

    def save(self, scenario: ScenarioPayload, ingestion_summary: Dict[str, int]) -> ScenarioResponse:
        payload = ScenarioResponse(
            scenario=scenario,
            ingested_at=datetime.utcnow().isoformat(),
            ingestion_summary=ingestion_summary,
        )
        path = self._path(scenario.scenario_id)
        path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        return payload

    def load(self, scenario_id: str) -> ScenarioResponse:
        path = self._path(scenario_id)
        if not path.exists():
            raise FileNotFoundError(f"Scenario '{scenario_id}' not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ScenarioResponse.model_validate(data)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def scenario_to_markdown(payload: ScenarioPayload) -> str:
    lines = [
        f"# Scenario {payload.scenario_id}",
        "",
        f"- Policy Type: {payload.policy_type}",
        f"- Peril: {payload.peril}",
    ]
    if payload.loss_date:
        lines.append(f"- Loss Date: {payload.loss_date}")
    if payload.carrier_profile:
        lines.append(
            f"- Carrier Profile: {payload.carrier_profile.type}"
            + (f" ({payload.carrier_profile.name})" if payload.carrier_profile.name else "")
        )
        if payload.carrier_profile.playbook:
            lines.append(f"- Playbook: {payload.carrier_profile.playbook}")
    lines.append("")
    lines.append("## Loss Summary")
    lines.append(payload.loss_summary.strip())
    lines.append("")

    if payload.coverage_profile:
        lines.append("## Coverage Profile")
        for item in payload.coverage_profile:
            line = f"- **{item.name}**"
            details = []
            if item.limit:
                details.append(f"Limit: {item.limit}")
            if item.deductible:
                details.append(f"Deductible: {item.deductible}")
            if item.notes:
                details.append(item.notes)
            if details:
                line += " (" + "; ".join(details) + ")"
            lines.append(line)
        lines.append("")

    if payload.evidence_items:
        lines.append("## Evidence & Documentation")
        for evidence in payload.evidence_items:
            lines.append(f"- **{evidence.title}**: {evidence.description or 'No description'}")
            if evidence.content:
                lines.append(f"  - Content: {evidence.content[:2000]}")
            if evidence.file_reference:
                lines.append(f"  - Reference: {evidence.file_reference}")
        lines.append("")

    if payload.open_tasks:
        lines.append("## Open Tasks")
        for task in payload.open_tasks:
            line = f"- [{task.status}] {task.task}"
            if task.owner:
                line += f" (Owner: {task.owner})"
            if task.notes:
                line += f" - {task.notes}"
            lines.append(line)
        lines.append("")

    if payload.risk_flags:
        lines.append("## Risk Flags")
        lines.extend(f"- {flag}" for flag in payload.risk_flags)
        lines.append("")

    if payload.freeform_notes:
        lines.append("## Additional Notes")
        lines.append(payload.freeform_notes.strip())
        lines.append("")

    return "\n".join(lines).strip()


def ingest_scenario_markdown(
    scenario: ScenarioPayload,
    ingestion_service: KnowledgeIngestionService,
) -> Dict[str, int]:
    markdown = scenario_to_markdown(scenario)
    paragraphs = split_into_paragraphs(markdown)
    if not paragraphs:
        return {"documents_written": 0, "nodes_written": 0, "edges_written": 0}

    chunks = chunk_text(paragraphs, chunk_size=INGEST_CHUNK_SIZE, overlap=INGEST_OVERLAP)
    group_id = f"scenario:{slugify(scenario.scenario_id)}"
    chunk_records: List[EpisodeChunk] = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_records.append(
            EpisodeChunk(
                chunk_id=f"{group_id}:{idx:04d}",
                text=chunk,
                metadata={
                    "scenario_id": scenario.scenario_id,
                    "policy_type": scenario.policy_type,
                    "peril": scenario.peril,
                    "carrier_type": scenario.carrier_profile.type,
                    "carrier_name": scenario.carrier_profile.name,
                },
            )
        )

    payload = EpisodePayload(
        episode_id=f"EP:{group_id}:{uuid.uuid4().hex[:8]}",
        source=f"scenario:{scenario.scenario_id}",
        source_type="scenario_markdown",
        reference_time=datetime.utcnow(),
        group_id=group_id,
        tags=["scenario", scenario.policy_type, scenario.peril],
        chunks=chunk_records,
        raw_text=markdown,
        metadata={
            "carrier_profile": scenario.carrier_profile.model_dump(),
        },
    )
    result = ingestion_service.ingest_episode(payload)
    return {
        "documents_written": result.documents_written,
        "nodes_written": result.nodes_written,
        "edges_written": result.edges_written,
        "episodes_written": result.episodes_written,
    }


def format_question_with_context(question: str, scenario: ScenarioPayload) -> str:
    lines = [
        f"Scenario ID: {scenario.scenario_id}",
        f"Policy Type: {scenario.policy_type}",
        f"Peril: {scenario.peril}",
    ]
    if scenario.carrier_profile:
        lines.append(f"Carrier Profile: {scenario.carrier_profile.type}")
        if scenario.carrier_profile.name:
            lines.append(f"Carrier Name: {scenario.carrier_profile.name}")
        if scenario.carrier_profile.playbook:
            lines.append(f"Playbook: {scenario.carrier_profile.playbook}")
    lines.append("")
    lines.append("Audience: Desk/field adjuster seeking expert peer guidance.")
    lines.append("Instruction: Respond as the expert adjuster advising another adjuster; never address the policyholder.")
    lines.append("")
    lines.append("Loss Summary:")
    lines.append(scenario.loss_summary)
    lines.append("")
    lines.append("Question:")
    lines.append(question)
    return "\n".join(lines)


def structure_adjuster_answer(
    cag: CAGAgent,
    scenario: ScenarioPayload,
    question: str,
    raw_answer: str,
) -> StructuredAdjusterAnswer:
    def _summary_with_explanation(
        summary: str,
        coverage: Dict[str, str],
        raw: str,
    ) -> str:
        summary_text = (summary or "").strip()
        explanation_markers = (
            "because",
            "based on",
            "due to",
            "since",
            "therefore",
            "under the policy",
            "pursuant to",
        )
        has_explanation = any(
            marker in summary_text.lower() for marker in explanation_markers
        )
        if has_explanation:
            return summary_text

        rationale = ""
        if isinstance(coverage, dict):
            for value in coverage.values():
                if isinstance(value, str) and value.strip():
                    rationale = value.strip()
                    break

        if not rationale:
            raw_lines = [
                line.strip()
                for line in (raw or "").splitlines()
                if line.strip()
                and not line.strip().startswith("[context excerpt]")
                and not line.strip().startswith("[Document")
            ]
            rationale = raw_lines[0] if raw_lines else ""

        if summary_text and rationale:
            return f"{summary_text} Explanation: {rationale}"
        if rationale:
            return f"Explanation: {rationale}"
        if summary_text:
            return (
                f"{summary_text} Explanation: This recommendation follows the "
                "policy context and claim facts provided."
            )
        return "Explanation: Recommendation based on provided policy context and claim facts."

    schema_prompt = """
Return JSON with the following structure:
{
  "summary": "one paragraph summary of the guidance that clearly explains why",
  "recommended_steps": ["ordered list of next actions"],
  "coverage_analysis": {"topic": "brief analysis"},
  "documentation_checklist": [
    {"item": "describe document", "status": "pending|received|not_applicable", "notes": ""}
  ],
  "citations": [
    {"source": "Document or Scenario reference", "details": "optional detail"}
  ]
}
Do not include any additional keys or commentary.
"""

    user_prompt = (
        f"Scenario ID: {scenario.scenario_id}\n"
        f"Policy Type: {scenario.policy_type}\n"
        f"Peril: {scenario.peril}\n"
        f"Carrier Type: {scenario.carrier_profile.type}\n"
        f"Question: {question}\n"
        f"Answer:\n{raw_answer}\n"
        "Produce the structured JSON."
    )

    try:
        response = cag.openai_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.0,
            messages=[
                {
                    "role": "system",
                    "content": "You convert answers into structured JSON for adjusters."
                               + schema_prompt,
                },
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        structured = json.loads(content)
    except Exception:
        structured = {
            "summary": raw_answer.strip(),
            "recommended_steps": [],
            "coverage_analysis": {},
            "documentation_checklist": [],
            "citations": [],
        }

    checklist = [
        DocumentationChecklistItem(**item)
        for item in structured.get("documentation_checklist", [])
        if isinstance(item, dict) and "item" in item
    ]
    citations = [
        Citation(**item)
        for item in structured.get("citations", [])
        if isinstance(item, dict) and "source" in item
    ]
    coverage_analysis = structured.get("coverage_analysis") or {}
    summary = _summary_with_explanation(
        structured.get("summary", raw_answer.strip()),
        coverage_analysis if isinstance(coverage_analysis, dict) else {},
        raw_answer,
    )
    recommended_steps = structured.get("recommended_steps", [])
    if not isinstance(recommended_steps, list):
        recommended_steps = []

    return StructuredAdjusterAnswer(
        scenario_id=scenario.scenario_id,
        question=question,
        summary=summary,
        recommended_steps=recommended_steps,
        coverage_analysis=coverage_analysis,
        documentation_checklist=checklist,
        citations=citations,
        raw_answer=raw_answer,
    )


# --------------------------------------------------------------------------- #
# FastAPI application
# --------------------------------------------------------------------------- #


app = FastAPI(title="Study Agents Scenario API", version="0.1.0")
_default_cors = ["http://localhost:5173", "http://127.0.0.1:5173"]
allowed_origins_env = os.getenv("SCENARIO_API_CORS")
if allowed_origins_env:
    if allowed_origins_env.strip() == "*":
        cors_config = {"allow_origins": ["*"], "allow_credentials": False}
    else:
        parsed = [origin.strip() for origin in allowed_origins_env.split(",") if origin.strip()]
        cors_config = {"allow_origins": parsed or _default_cors, "allow_credentials": True}
else:
    cors_config = {"allow_origins": _default_cors, "allow_credentials": True}

app.add_middleware(
    CORSMiddleware,
    allow_methods=["*"],
    allow_headers=["*"],
    **cors_config,
)
scenario_repo = ScenarioRepository(SCENARIO_STORAGE_DIR)


def _resolve_supabase_runtime() -> tuple[str, str]:
    settings = get_settings()
    settings.require_groups("supabase")

    supabase_url = (
        os.getenv("SCENARIO_SUPABASE_URL", "").strip() or settings.supabase_url
    )
    supabase_key = (
        os.getenv("SCENARIO_SUPABASE_KEY", "").strip() or settings.supabase_key
    )
    return supabase_url, supabase_key


_scenario_supabase_url, _scenario_supabase_key = _resolve_supabase_runtime()
_scenario_ingestion_config = IngestionConfig.from_env(
    supabase_url=_scenario_supabase_url,
    supabase_key=_scenario_supabase_key,
)

ingestion_service = KnowledgeIngestionService(
    ingestion_config=_scenario_ingestion_config
)
cag_agent = CAGAgent(
    supabase_url=_scenario_supabase_url,
    supabase_key=_scenario_supabase_key,
)


@app.post("/scenarios", response_model=ScenarioResponse)
def create_scenario(payload: ScenarioPayload) -> ScenarioResponse:
    try:
        ingestion_summary = ingest_scenario_markdown(payload, ingestion_service)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc
    result = scenario_repo.save(payload, ingestion_summary)
    return result


@app.get("/scenarios/{scenario_id}", response_model=ScenarioResponse)
def get_scenario(scenario_id: str) -> ScenarioResponse:
    try:
        return scenario_repo.load(scenario_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/scenarios/{scenario_id}/questions",
    response_model=StructuredAdjusterAnswer,
)
def answer_scenario_question(scenario_id: str, payload: ScenarioQuestion) -> StructuredAdjusterAnswer:
    try:
        scenario_response = scenario_repo.load(scenario_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    scenario = scenario_response.scenario
    formatted_question = format_question_with_context(payload.question, scenario)
    context, raw_answer = cag_agent.answer_with_enhanced_cag(formatted_question)
    if context.strip():
        raw_answer = f"{raw_answer}\n\n[context excerpt]\n{context[:800]}"
    structured = structure_adjuster_answer(cag_agent, scenario, payload.question, raw_answer)
    return structured


def main() -> None:
    import uvicorn

    reload_enabled = os.getenv("SCENARIO_API_RELOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    uvicorn.run(
        "study_agents.scenario_api:app",
        host=os.getenv("SCENARIO_API_HOST", "0.0.0.0"),
        port=int(os.getenv("SCENARIO_API_PORT", "9000")),
        reload=reload_enabled,
    )
