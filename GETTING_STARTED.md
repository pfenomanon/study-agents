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
- outbound network access to OpenAI and either:
  - Supabase Cloud, or
  - local Supabase Docker stack (via Supabase CLI)
- optional: Ollama endpoint access if you plan to use Ollama runtime

### 3.2 Choose Supabase Mode (Cloud or Local Docker)

You must choose one Supabase mode before starting app services.

Mode A: Supabase Cloud (managed)
- Use your hosted project URL/key values.
- Set in `.env`:
  - `SUPABASE_URL=https://<project-ref>.supabase.co`
  - `SUPABASE_KEY=<service_role_key>`
- Run schema in cloud project:
  - `backend-vps/supabase_schema.sql`

Mode B: Local Supabase in Docker (self-hosted on VPS)
- Run from `backend-vps`:
  - `chmod +x scripts/setup_local_supabase.sh`
  - `./scripts/setup_local_supabase.sh`
- This starts Supabase containers and updates `.env` with local `SUPABASE_URL`/`SUPABASE_KEY`.
- The helper prefers a service-role key when available; this is needed for full write/ingestion capability.

Typical local Supabase containers started by `supabase start` include:
- `supabase_db_*` (Postgres)
- `supabase_kong_*` (API gateway)
- `supabase_auth_*` (GoTrue)
- `supabase_rest_*` (PostgREST)
- `supabase_realtime_*`
- `supabase_storage_*`
- `supabase_studio_*`
- `supabase_pg_meta_*`
- `supabase_analytics_*` (Logflare)
- `supabase_inbucket_*`
- `supabase_vector_*`

Inspect what is running:

```bash
supabase status
docker ps --format '{{.Names}}\t{{.Image}}' | rg -i 'supabase|study-agents'
```

### 3.3 Clone and Configure

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
cp .env.example .env
```

Edit `.env` and set at minimum:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`

Important:
- For backend/server usage, `SUPABASE_KEY` should be a service-role key.
- If you use local Supabase and the helper had to fall back to anon key, replace it with service-role for full ingestion and write features.

Recommended security hardening:
- `API_TOKEN` for `/cag-answer` and `/cag-ocr-answer`
- `COPILOT_API_KEY` for `/copilot/*`

### 3.4 Create Supabase Schema

Run `backend-vps/supabase_schema.sql` in the Supabase target selected above (cloud or local).

### 3.5 Start Services

```bash
docker compose up -d --build
docker compose ps
```

Default exposed ports:
- `8000` `cag-service` (`/cag-answer`, `/cag-ocr-answer`)
- `8100` `rag-service` (`/build`)
- `9000` `scenario-service` (Scenario API)
- `9010` `copilot-service` (`/copilot/chat`, `/copilot/capture`, `/copilot/cag-process`)

### 3.6 What Runs in Docker

App stack (`backend-vps/docker-compose.yml`):
- `cag-service` (port `8000`)
- `rag-service` (port `8100`)
- `utility-service`
- `copilot-service` (port `9010`)
- `scenario-service` (port `9000`)
- `vault` (dev mode, optional)

If using local Supabase mode, those Supabase containers run in parallel as a separate stack managed by Supabase CLI.

### 3.7 Smoke Tests

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

### 3.8 Day-2 Operations

```bash
docker compose logs -f cag-service
docker compose restart cag-service
docker compose down
```

### 3.9 Security Baseline

- Keep Supabase local ports bound to localhost unless you intentionally proxy them.
- Do not expose Supabase Postgres/API ports directly to the public internet.
- Keep only required app ports open externally (`8000`, `8100`, `9000`, `9010` as needed).
- Always set `API_TOKEN` and `COPILOT_API_KEY` in non-local deployments.
- Rotate keys if shared accidentally.

### 3.10 Authentication and Encrypted Communication

Authentication:
- `cag-service` (`/cag-answer`, `/cag-ocr-answer`) enforces token auth when `API_TOKEN` is set.
- `copilot-service` (`/copilot/*`) enforces token auth when `COPILOT_API_KEY` is set.
- Accepted client headers:
  - `X-API-Key: <token>`
  - `Authorization: Bearer <token>`
- `scenario-service` (`/scenarios*`) has no built-in token guard in this split package; protect it with network controls and/or reverse-proxy auth.

Encryption in transit:
- The default compose publishes plain HTTP ports (`8000`, `8100`, `9000`, `9010`).
- For internet-facing deployments, terminate TLS in front of these services (Caddy/Nginx/Traefik/ALB).
- Use `https://` URLs from remote clients; do not expose Supabase Docker ports publicly.

## 4) Client Install (Windows PC and macOS)

Use `local-run/` when running screenshot capture on an end-user machine and sending OCR/questions to the VPS APIs.

### 4.1 Windows PC (no preinstalled dependencies assumed)

If Git is not installed yet, install it first:

```powershell
winget install --id Git.Git -e --source winget
```

If the user already has the repository cloned, one-liner install from repo root:

```cmd
cd local-run && install_client.bat
```

Fresh-machine one-liner from PowerShell (install + clone + setup):

```powershell
winget install --id Git.Git -e --source winget; git clone git@github.com:pfenomanon/study-agents.git; cd study-agents\local-run; cmd /c install_client.bat
```

Note: this repository is private. The clone command requires a GitHub account with access and an SSH key already added to that account.

The installer attempts:
- Python 3.11 install via `winget` if missing
- virtualenv creation
- dependency install in `local-run/study-agents/.venv`

Configure:
- `local-run/client_config.bat`

Set:
- `VPS_BASE_URL=https://<your-domain-or-ip>`
- `REMOTE_API_TOKEN=<token>` if backend `API_TOKEN` is enabled

Run (from `local-run`):
- `run_remote_image.bat` (preferred)
- `run_remote_text.bat` (fallback)
- `test_remote_api.bat` (connectivity check)

### 4.2 macOS (no preinstalled dependencies assumed)

If the user already has the repository cloned, one-liner install from repo root:

```bash
cd local-run && chmod +x install_client_macos.sh && ./install_client_macos.sh
```

Fresh-machine one-liner from Terminal (install + clone + setup):

```bash
command -v brew >/dev/null 2>&1 || NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; (eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"; brew install git); git clone git@github.com:pfenomanon/study-agents.git; cd study-agents/local-run; chmod +x install_client_macos.sh; ./install_client_macos.sh
```

Note: this repository is private. The clone command requires a GitHub account with access and an SSH key already added to that account.

The installer attempts:
- Homebrew install if missing
- Python 3.11 install via Homebrew if missing
- Git install via Homebrew if missing
- virtualenv creation
- dependency install in `local-run/study-agents/.venv`
- `client_config.sh` creation from template

Configure:
- `local-run/client_config.sh`

Set:
- `VPS_BASE_URL=https://<your-domain-or-ip>`
- `REMOTE_API_TOKEN=<token>` if backend `API_TOKEN` is enabled

Run (from `local-run`):
- `./run_remote_image.sh` (preferred)
- `./run_remote_text.sh` (fallback)
- `./test_remote_api.sh` (connectivity check)

macOS permission note:
- Grant Screen Recording (and optionally Accessibility) to Terminal/iTerm in:
  - System Settings -> Privacy & Security

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
