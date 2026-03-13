# Study Agents

Local agent stack for PDF RAG + vision-driven subject-matter-expert assistance, covering RAG chunking, CAG knowledge graph enrichment, Supabase vector storage, and MCP tooling for downstream question answering.

> For fresh clone/pull onboarding, use the root `GETTING_STARTED.md` first. This README contains additional implementation detail and historical notes, and may reference components from the original monorepo that are not shipped in this split deployment package.

## Quick Links
- `AGENTS_EXECUTION_PLAN.md`: execution order, system prompt, and what each agent does.
- `AGENTS_COMPLETE_GUIDE.md`: deep-dive docs + troubleshooting.
- `supabase_schema.sql`: create new Supabase tables/function before ingesting data.

## Packaging & Deployment Helpers

- `package-linux.sh`: packages the repo into `dist/study-agents-<timestamp>.tar.gz`.
- `package-windows.ps1`: builds `dist/study-agents-<timestamp>.zip`.
- `scripts/build_release_bundles.py`: builds two reproducible multi-host artifacts:
  - `dist/study-agents-backend-vps-<timestamp>.tar.gz`
  - `dist/study-agents-windows-client-<timestamp>.zip`
  - plus `dist/DEPLOYMENT-QUICKSTART-<timestamp>.md` with copy/paste commands.
- `scripts/install_backend_vps.sh`: one-script backend installer/runner for new VPS hosts (`deps`, `start`, `status`, `logs`, `stop`).
- `docker-compose.yml`: builds/runs the multi-service stack (CAG API 8000, RAG builder 8100, Scenario API 9000, Copilot API 9010, plus a utility image for CLIs).
- `docker/*.Dockerfile`: per-service images; all include the `[full]` extra (vision/OCR). The root `Dockerfile` is a slim MCP/CLI image if you want a single-container runtime.

## Graph Inspector & Visualization

- `study-agents-graph-inspector` (and the MCP `inspect_graph` tool) exports `knowledge_graph/graph_inspector.{md,mmd}`, generates `graph_inspector.svg`, and runs `answer_with_cag` on your verification question.
- Use `knowledge_graph/graph_inspector.mmd` in Mermaid.live or run `mmdc` (`npm install -g @mermaid-js/mermaid-cli`) to recreate the SVG.

## CAG HTTP API & Thin Client

- `study-agents-api` runs a small HTTP server inside the Docker container (port `8000`) that exposes:
  - `POST /cag-answer` – body `{"question": "..."}` -> CAGAgent.enhanced_retrieve_context (vector + knowledge graph) -> answer JSON.
  - `POST /cag-ocr-answer` – multipart `image=@screenshot.png` -> OCR + CAGAgent.enhanced_retrieve_context -> answer JSON.
- Every successful call appends a Markdown entry to `data/qa_sessions/qa_log.md` with:
  - Timestamp, source (`cag-text` or `cag-ocr`), question, model answer, context snippet, and a `User Correction`/`Status` field you can edit later.
- The Compose `cag-service` runs the API by default; restart it with `docker compose restart cag-service` or rerun the entrypoint via `docker compose exec cag-service study-agents-api`.

### Vision Agent Modes & Margins

- Run the vision agent as a module so you can pass margins in inches (converted using `--dpi`, default 96):
  - `python -m study_agents.vision_agent --dpi 96 --top-in 1.0 --left-in 0.5 --right-in 0.5 --bottom-in 1.0`
- Modes are controlled by `REMOTE_MODE` (env) or `--mode`:
  - `local` (default): screenshot -> OCR -> **local CAGAgent.enhanced_retrieve_context** (vector + KG) -> Ollama cloud reasoning.
  - `remote`: screenshot -> OCR -> text question sent to `/cag-answer` on the VPS.
  - `remote_image`: screenshot uploaded as image to `/cag-ocr-answer` on the VPS (OCR + CAG fully remote).
- Example one-liners (Windows CMD):
  - Remote text:  
    `set REMOTE_MODE=remote && set REMOTE_CAG_URL=http://<vps-ip>:8000/cag-answer && python -m study_agents.vision_agent --dpi 96 --top-in 1.0 --left-in 0.5 --right-in 0.5 --bottom-in 1.0`
  - Remote image:  
    `set REMOTE_MODE=remote_image && set REMOTE_IMAGE_URL=http://<vps-ip>:8000/cag-ocr-answer && python -m study_agents.vision_agent --dpi 96 --top-in 1.0 --left-in 0.5 --right-in 0.5 --bottom-in 1.0`

### KB Capture Agent (Screenshot → Markdown)

- Supports the same inch-based margins as the vision agent plus a pure “extract only” mode:
  ```bash
  python -m study_agents.kb_capture_agent \
    --dpi 96 \
    --top-in 2.25 --left-in 7.25 --right-in 4 --bottom-in 2.25 \
    --filename data/output/notes/asphalt-shingle-damage.md \
    --no-cag --extract-only
  ```
- `--extract-only` skips all answering logic and simply appends the Markdown transcription to the target file (the parent directory is created automatically). Drop the flag if you still want the CAG/RAG answer appended under `## Answer`.
- You can still pass `--region x y width height` in pixels if you prefer explicit coordinates.

### Web Research Agent (Crawl + Downloads)

- Crawl a seed URL, score relevance (heuristic or LLM), save Markdown summaries, optionally download linked PDFs/documents, **resume interrupted crawls**, and **auto-ingest high-value pages** into Supabase:
  ```bash
  python -m study_agents.web_research_agent \
    "https://www.tdi.texas.gov/pubs/consumer/cb025.html" \
    5 200 \
    --outdir research_output/tx_homeowner2 \
    --query "texas homeowner insurance coverage requirements" \
    --llm-relevance \
    --download-docs \
    --max-seconds 300 \
    --auto-ingest \
    --ingest-threshold 0.6 \
    --resume-file research_output/tx_homeowner2/.resume.json
  ```
- New flags:
  - `--query` feeds the relevance LLM with your research topic.
  - `--llm-relevance` toggles reasoning-based scoring (falls back to heuristics if the call fails).
  - `--download-docs` saves referenced PDFs/DOCX/etc. into `outdir/downloads` and emits a `downloads_manifest.json`.
  - `--max-seconds` stops the crawl after the specified time even if depth/page limits haven’t been reached.
  - `--auto-ingest` enables automatic Supabase ingestion for pages meeting `--ingest-threshold` (defaults to 0.5). Chunk sizing/overlap can be tuned via `--ingest-chunk-size` / `--ingest-overlap`, and `--ingest-group` prefixes the Supabase `group_id`.
  - `--resume-file` persists crawl state (queue + visited set) so you can restart long crawls later. Pair with `--resume-reset` to discard existing state.

### Curation Workflow

- To correct bad answers:
  - Edit `data/qa_sessions/qa_log.md` and update `User Correction:` and `Status:` (e.g. to `corrected`).
  - A future curation script can scan this log and upsert corrected Q&A into Supabase and the knowledge graph without re-chunking PDFs.

## Prompt Customization

- All system prompts live under `prompts/`:
  - `vision_reasoning.txt`, `cag_entity_extraction.txt`, `cag_relationship_extraction.txt`, `cag_answer_generation.txt`, `cag_cluster_topic.txt`
  - `markdown_system_prompt.md` (KB capture), `web_research_system_prompt.md`
- Edit these files (or set `PROMPTS_DIR=/path/to/prompts`) to tweak agent behavior without touching code. Changes take effect the next time the agent runs.
- For safe domain-wide personalization, use the profile wizard to regenerate `prompts/kg_entity_extraction.txt`, `prompts/kg_edge_extraction.txt`, and `prompts/vision_reasoning.txt` with schema guardrails:
  ```bash
  python3 scripts/domain_wizard.py --interactive --profile-name <your-domain> --apply --check
  ```
  Profiles live in `domain/profiles/`; base templates live under `prompts/templates/`.

## Docker Compose

Use `docker compose up --build` from this directory to spin up **per-agent services**:

- `cag-service` (port 8000): runs `study_agents.api_server` (`/cag-answer`, `/cag-ocr-answer`) and writes `data/qa_sessions/qa_log.md`.
- `rag-service` (port 8100): exposes `POST /build` to trigger the reasoning-driven RAG bundle builder.
- `utility-service`: base image kept running via `tail -f /dev/null` so you can `docker compose run utility-service ...` for any one-off CLI agent (web research, RAG ingestion, etc.) without rebuilding images.
- `copilot-service` (port 9010): PydanticAI backend; now exposes `/copilot/capture` for capture + OCR + answer.
- `scenario-service` (port 9000): Scenario API (`study_agents.scenario_api`) for structured scenario ingestion and Q&A.

All services mount `.env`, `prompts/`, and the relevant `data/` folders so you can edit prompts or documents on the host and the containers see the changes immediately.

Example interactions:

```bash
# Build and start everything
docker compose up -d --build

# Test CAG HTTP API
curl -X POST http://localhost:8000/cag-answer \
  -H "Content-Type: application/json" \
  -d '{"question": "What are the eligibility requirements for TWIA coverage?"}'

# Trigger RAG builder via HTTP
curl -X POST http://localhost:8100/build \
  -H "Content-Type: application/json" \
  -d '{"pdf_path": "/app/data/pdf/TWIA-Commercial-Policy-HB-3208.pdf", "outdir": "/app/data/output", "push": true}'

# Run the web research crawler inside the utility service
docker compose run --rm utility-service \
  python -m study_agents.web_research_agent \
    "https://www.tdi.texas.gov/pubs/consumer/cb025.html" 5 200 \
    --outdir /app/research_output/tx_homeowner2 \
    --query "texas homeowner insurance coverage requirements" \
    --download-docs --max-seconds 300
```

## MCP Tools

When you run `study-agents-mcp`, the following tools become available to MCP clients (Claude Desktop, Windsurf, etc.):
- `capture_question`: run the local vision agent to capture/answer on-screen questions.
- `build_rag_bundle`: execute the reasoning-driven PDF → RAG pipeline (with optional Supabase push).
- `inspect_graph`: regenerate the Mermaid/CSV exports and ask a verification question.
- `web_research_crawl`: invoke the upgraded crawler (supports `--query`, `--llm-relevance`, downloads, and time limits) and write Markdown outputs under `research_output/`.
- `kb_extract_from_image`: run the KB capture OCR pipeline on an existing image, returning Markdown (and optionally appending it to a `.md` file or invoking CAG for an answer).
- `copilot_vision_capture`: via Copilot service `/copilot/capture`, capture a screen region (local/remote/remote_image) and run OCR + answer. Useful for GUI hosts; headless requires a virtual display.

Start the MCP server with:
```bash
study-agents-mcp
```

## Getting Started

1. Copy `.env.example` to `.env` and fill in your keys/models/URLs.
2. Install the package with the extras you need:
   - Core CLI/API only: `pip install -e .`
   - Screenshot/vision tooling: `pip install -e .[vision]`
   - Everything: `pip install -e .[full]`
3. Validate your environment before launching any agents:
   ```bash
   study-agents-validate --print-summary
   ```
4. Run `supabase_schema.sql` in your Supabase target (cloud or local).
5. Start the pieces you need:
   - Individually (legacy): `study-agents-rag`, `study-agents-cag`, `study-agents-graph-inspector`, `study-agents-api`
   - Orchestration helper: `study-agents-manage run --services cag,api`

Optional: set `USE_HYBRID_RETRIEVAL=true` in `.env` to enable the hybrid retrieval
layer (semantic + BM25 + graph). Leave it unset to keep the legacy vector-only flow while
you test the new pipeline.

### Updated defaults and ingestion path
- Docling PDF/OCR extraction is enabled by default (`RAG_USE_DOCLING=true`). The Docker images now install the `[full]` extra to include vision/OCR dependencies.
- The CAG CLI routes documents through the unified kg_pipeline Episode → Extraction → Supabase flow (grouped by document slug) to avoid duplicate nodes/edges.
- Web Research Agent no longer hard-requires Ollama; if `OLLAMA_HOST`/`OLLAMA_API_KEY` are missing, it falls back to heuristic scoring/link extraction instead of failing at import time.

## Installation Profiles & Validation

- `pip install -e .` keeps the base install focused on Supabase/OpenAI/RAG tooling (Docling is still included for PDF/OCR flows).
- Add extras as needed:
  - `.[vision]` → Screen capture, OCR fallbacks, and keyboard hooks.
  - `.[full]` → Convenience alias for `.[vision]` (install once for hosts that need every feature).

Use `study-agents-validate --groups openai,supabase,ollama` to fail fast when keys/URLs are missing. The command also ensures required directories exist and can print a short summary for deployment logs.

## Process Orchestrator

`study-agents-manage` provides a single entry point for validation and keeping long-running services alive:

```bash
# Show summary and validate OpenAI + Supabase config
study-agents-manage status --validate

# Launch CAG + API and keep them tied to this terminal
study-agents-manage run --services cag,api

# Validate specific groups (e.g., Ollama only)
study-agents-manage validate --groups ollama
```

Press `Ctrl+C` to stop every managed process gracefully.

## Vision Capture (Copilot UI or API)
- UI: visit `http://<host>:3000`, use the Vision Capture card to set mode (`local`, `remote`, `remote_image`), monitor, and optional region, then click **Run capture**. Needs a display; on headless servers, use a virtual display (Xvfb) or run the UI on a desktop/WSLg host.
- API: `POST http://<host>:9010/copilot/capture` with JSON like `{"monitor":1,"mode":"local","region":{"top":0,"left":0,"width":1200,"height":800}}`. For `remote_image`, set `remote_image_url` to `http://cag-service:8000/cag-ocr-answer`.

## Security Hardening
- Set API keys for service access: `API_TOKEN` (CAG HTTP API) and `COPILOT_API_KEY` (Copilot service). Clients must send `X-API-Key` or `Authorization: Bearer <token>`.
- `scenario-service` (`/scenarios*`) is not token-guarded by default in this split package; protect it with reverse-proxy auth and/or private networking.
- Image uploads are size/type limited (`MAX_UPLOAD_BYTES`, PNG/JPEG only). Keep endpoints behind a reverse proxy with TLS and IP allowlists where possible.
- Compose service ports are HTTP by default (`8000/8100/9000/9010`); terminate TLS for any external/client-facing access.
- Containers drop root privileges (`USER app`). Bind only required ports; the default compose uses an internal `backend` network.
- Vault (optional, OSS): a Vault dev service is included in compose. If `VAULT_ADDR`/`VAULT_TOKEN` are set, containers will attempt to fetch secrets from `kv/data/study-agents/*` via `scripts/use_env.sh` and render `/env/.env.runtime`. Default is dev/root token; replace with a secure Vault deployment for production.

## Scenario Orchestration API (FastAPI)

Use the Scenario API to submit structured scenarios and retrieve subject-matter-expert responses in a consistent JSON envelope.
By default it uses the same `SUPABASE_URL` / `SUPABASE_KEY` as the rest of `study-agents`; optional `SCENARIO_SUPABASE_URL` / `SCENARIO_SUPABASE_KEY` overrides are available if needed.

```bash
# Inside the repo or utility container
study-agents-scenario-api
# or explicitly
uvicorn study_agents.scenario_api:app --host 0.0.0.0 --port 9000
```

Endpoints:

- `POST /scenarios` – Accepts `scenario_id`, scenario/context profile, optional carrier metadata, risk flags, etc. The payload is transformed into markdown, ingested via `KnowledgeIngestionService`, and persisted under `data/scenarios/`.
- `GET /scenarios/{scenario_id}` – Returns the stored scenario alongside the most recent ingestion summary.
- `POST /scenarios/{scenario_id}/questions` – Invokes the CAG agent with the correct playbook context and responds with a structured JSON object:
  ```json
  {
    "scenario_id": "AUTO-TX-042",
    "question": "What documentation is still required?",
    "summary": "...",
    "recommended_steps": ["Step 1", "Step 2"],
    "analysis": {"topic_a": "...", "topic_b": "..."},
    "coverage_analysis": {"topic_a": "...", "topic_b": "..."},
    "documentation_checklist": [
      {"item": "Police report", "status": "pending"},
      {"item": "Contractor estimate", "status": "received"}
    ],
    "citations": [{"source": "scenario:AUTO-TX-042", "details": "loss summary"}],
    "raw_answer": "Full LLM answer + trimmed context."
  }
  ```

Carrier profile metadata is optional. Provide `carrier_profile.type="carrier"` plus `name`/`playbook` only when you want carrier/workflow-specific terminology overlays.

## Web GUI (React + Vite)

The `frontend/` directory contains a secure-by-default React/TypeScript SPA that talks to the Scenario API. It enforces HTTPS (configure your reverse proxy accordingly), uses token-based fetch calls, and never exposes secrets in client code.

```bash
cd frontend
npm install
VITE_SCENARIO_API_URL=https://<api-domain> npm run dev
# Build for production
VITE_SCENARIO_API_URL=https://<api-domain> npm run build
```

- `ScenarioForm` lets you capture structured scenarios (scenario_id, coverage items, carrier profile, risk flags). Each line in the coverage/evidence fields follows `Name|Limit|Deductible|Notes` to keep the UI compact while still producing structured data for the API.
- `ScenarioQuestion` posts questions to `/scenarios/{id}/questions` and renders structured answers (summary, steps, analysis, documentation checklist, citations).
- `ScenarioView` shows the currently active scenario, ingestion stats, and the most recent subject-matter-expert response (with expandable raw answer + context).

Deploy the built assets behind HTTPS (Traefik/Nginx). Set `SCENARIO_API_CORS` on the API so only the GUI origin is permitted.

## Testing

- Use the scripts under `tests/` or run `study-agents-graph-inspector --question "..."`.
# Local Supabase (optional)

If you prefer to run Supabase locally on the VPS instead of the cloud project, use the helper script:

```bash
cd /home/study-agents
chmod +x scripts/setup_local_supabase.sh
./scripts/setup_local_supabase.sh
```

The script:
1. Installs the Supabase CLI if it is missing.
2. Runs `supabase start` (launching the full Supabase stack via Docker).
3. Reads the REST URL + key from `supabase status --json` and updates `.env`.
   - It prefers `service_role` key (recommended for backend write/ingestion features).
   - If only anon key is discoverable, it uses anon and prints a warning.

Typical local Supabase containers include:
- Postgres (`supabase_db_*`)
- API gateway (`supabase_kong_*`)
- Auth (`supabase_auth_*`)
- REST (`supabase_rest_*`)
- Realtime (`supabase_realtime_*`)
- Storage (`supabase_storage_*`)
- Studio (`supabase_studio_*`)
- PG Meta (`supabase_pg_meta_*`)
- Analytics (`supabase_analytics_*`)
- Inbucket (`supabase_inbucket_*`)
- Vector (`supabase_vector_*`)

Inspect runtime services with:

```bash
supabase status
docker ps --format '{{.Names}}\t{{.Image}}' | rg -i 'supabase|study-agents'
```

Supabase Studio (GUI) and APIs are exposed on `http://127.0.0.1:5432x` ports (the CLI output lists the exact URLs). Tunnel or proxy those ports if you need remote access.

## One-Step Bootstrap

On a fresh VPS (after copying the bundle ZIP and extracting it somewhere like `/home/bootstrap`), run:

```bash
cd /path/to/extracted/bundle
sudo ./bootstrap.sh
```

The script will:
1. Detect the embedded `study-agents-*.zip`, unpack it into `/home/study-agents` (if not already present).
2. Install Docker/Docker Compose, the Supabase CLI, and `unzip` as needed.
3. Start the local Supabase stack and update `.env`.
4. Stop/remove any stale project containers that might be holding ports 8000/8100.
5. Run `docker compose up -d --build`.

When it finishes, the repo lives in `/home/study-agents` and `docker compose ps` will show the app services (`cag-service`, `rag-service`, `scenario-service`, `copilot-service`, `utility-service`) running.
If Supabase’s official installer is unreachable, the script automatically falls back to downloading the latest CLI binary from GitHub releases as a fallback.

## Rebuild the Bootstrap Bundle

Whenever you need a fresh distributable ZIP, run this from the repo root (on your dev machine):

```bash
python scripts/build_bootstrap_package.py
```

The script regenerates two archives in `dist/` using LF-only shell scripts with executable bits preset:

- `study-agents-YYYYMMDD-HHMMSS.zip` – a full snapshot of the repo (minus `dist/` and build caches).
- `bootstrap-package-YYYYMMDD-HHMMSS.zip` – the bundle you upload to the VPS; it already contains `bootstrap.sh`, `setup_local_supabase.sh`, the docs, and the embedded project ZIP.

Upload the latter archive to the VPS, extract it anywhere, and run `sudo ./bootstrap.sh` exactly once.
