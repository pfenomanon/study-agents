# Study Agents: Installation and Personalization Guide

This guide is the authoritative setup path for users who `git clone` this repository and run it in their own environment.

It covers:
- backend installation on Linux/VPS with Docker Compose
- Windows client installation for remote capture
- runtime data behavior (what is auto-created, what is not in Git)
- `.env` configuration details
- insurance-specific prompts and code areas to personalize for non-insurance use cases

## 1) Repository Layout

- `backend-vps/`: Linux backend package (APIs/services/prompts/scripts)
- `local-run/`: Windows/local client package for remote capture workflows

## 2) Runtime Data and Bind Mount Behavior

In `backend-vps/docker-compose.yml`, several host folders are bind-mounted into containers:
- `./data -> /app/data`
- `./knowledge_graph -> /app/knowledge_graph`
- `./research_output -> /app/research_output`

What this means:
- these folders are created automatically on the host if missing when Docker starts services
- they start empty in a fresh clone
- generated outputs (screenshots, OCR outputs, downloaded docs, scenario JSON, etc.) are written there at runtime
- they are local to that machine unless someone explicitly commits them to Git

This repo now ignores these runtime folders in `.gitignore`.

## 3) Backend Install (Linux/VPS)

### 3.1 Prerequisites

- Docker Engine
- Docker Compose v2 plugin
- outbound network access to OpenAI and Supabase
- optional: Ollama endpoint access if you plan to use Ollama runtime

### 3.2 Clone and Configure

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
cp .env.example .env
```

Edit `.env` and set at minimum:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`

Recommended security hardening:
- `API_TOKEN` for `/cag-answer` and `/cag-ocr-answer`
- `COPILOT_API_KEY` for `/copilot/*`

### 3.3 Create Supabase Schema

Run `backend-vps/supabase_schema.sql` in your Supabase SQL editor (or via your database connection workflow).

### 3.4 Start Services

```bash
docker compose up -d --build
docker compose ps
```

Default exposed ports:
- `8000` `cag-service` (`/cag-answer`, `/cag-ocr-answer`)
- `8100` `rag-service` (`/build`)
- `9000` `scenario-service` (Scenario API)
- `9010` `copilot-service` (`/copilot/chat`, `/copilot/capture`, `/copilot/cag-process`)

### 3.5 Smoke Tests

Without API token:

```bash
curl -X POST http://localhost:8000/cag-answer \
  -H "Content-Type: application/json" \
  -d '{"question":"Return OK if service is reachable."}'
```

With API token enabled:

```bash
curl -X POST http://localhost:8000/cag-answer \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <API_TOKEN>" \
  -d '{"question":"Return OK if service is reachable."}'
```

### 3.6 Day-2 Operations

```bash
docker compose logs -f cag-service
docker compose restart cag-service
docker compose down
```

## 4) Windows Client Install (`local-run/`)

Use this when running screen capture on a Windows machine and sending questions/images to the VPS.

```cmd
cd local-run
install_client.bat
```

Then edit:
- `local-run/client_config.bat`

Set:
- `VPS_BASE_URL=http://<your-vps-host>:8000`
- `REMOTE_API_TOKEN=<token>` if backend `API_TOKEN` is set

Run:
- `run_remote_image.bat` (preferred)
- `run_remote_text.bat` (fallback)
- `test_remote_api.bat` (connectivity check)

## 5) `.env` Parameter Reference

See:
- `backend-vps/.env.example`
- `local-run/study-agents/.env.example`

Important groups:

- Required core:
  - `OPENAI_API_KEY`
  - `SUPABASE_URL`
  - `SUPABASE_KEY`
- Auth/security:
  - `API_TOKEN`
  - `COPILOT_API_KEY`
  - `MAX_UPLOAD_BYTES`
- Model/runtime:
  - `REASON_MODEL`
  - `REASON_PLATFORM` (`openai` or `ollama`)
  - `OLLAMA_HOST`, `OLLAMA_API_KEY`, `OLLAMA_TARGET`
- Retrieval/ingestion:
  - `USE_HYBRID_RETRIEVAL`
  - `RAG_USE_DOCLING`
  - `SUPABASE_DOCS_TABLE`, `SUPABASE_NODES_TABLE`, `SUPABASE_EDGES_TABLE`
- Prompt and path overrides:
  - `PROMPTS_DIR`
  - `QA_LOG_DIR`
  - `TEMP_IMAGE_DIR`
  - `SCENARIO_STORAGE_DIR`
- Scenario API:
  - `SCENARIO_API_HOST`, `SCENARIO_API_PORT`, `SCENARIO_API_CORS`
  - `SCENARIO_SUPABASE_URL`, `SCENARIO_SUPABASE_KEY`

## 6) Personalization for Non-Insurance Use Cases

The core RAG/CAG pipeline is reusable, but some defaults are insurance-oriented.

### 6.1 Prompt Files You Should Review First

Under `backend-vps/prompts/`:

- `vision_reasoning.txt`
  - currently starts with Texas insurance adjuster persona
- `kg_entity_extraction.txt`
  - currently optimized for insurance-study entities and examples
- `cag_answer_generation.txt`
  - generic, but still includes multiple-choice wording
- `kg_edge_extraction.txt`
  - "regulated industries" framing (may be fine, adjust if needed)

Usually domain migration is done by editing these prompt files first.

### 6.2 Insurance-Specific Code Paths to Know

These are code-level defaults (not prompt files):

- `backend-vps/src/study_agents/scenario_api.py`
  - schema and output structure are claims/coverage/adjuster-oriented
- `backend-vps/src/study_agents/cag_agent.py`
  - fallback `_DEFAULT_ANSWER_PROMPT` is insurance-adjuster specific (used only if prompt file missing)
- `backend-vps/src/study_agents/mcp_server_fixed.py`
  - default graph question references TWIA coverage
- `backend-vps/src/study_agents/rag_builder_core.py`
  - generated quick-test text uses insurance examples

If your target domain is not insurance, start with prompts; then refactor `scenario_api.py` if you plan to use scenario workflows.

### 6.3 Suggested Personalization Order

1. Update prompts in `backend-vps/prompts/`.
2. Run smoke tests against `/cag-answer`.
3. Ingest your own domain data into `data/` and run RAG build.
4. If using Scenario API, replace insurance schema/wording in `scenario_api.py`.
5. Re-test with domain-specific sample questions.

## 7) Data Safety and GitHub

- Runtime folders are local and git-ignored.
- Docker images are built from selected `COPY` inputs in Dockerfiles.
- A `.dockerignore` is included in `backend-vps/` to keep runtime data out of build context.

Bottom line: cloned users do not receive your local runtime outputs unless those files were explicitly committed.
