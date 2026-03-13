# Deployment Guide (Linux & AWS)

For clone/pull onboarding in this split deployment repo, start with `../GETTING_STARTED.md`.

This repo can be moved to any Linux host (including AWS EC2) and run either natively or via Docker Compose.

## Prereqs
- Python 3.11+
- Docker + Docker Compose v2 (optional but recommended)
- Network access to OpenAI/Ollama (if used) and Supabase
- Ports to expose (typical): 8000 (CAG API), 8100 (RAG service), 9000 (Scenario API)
- Display note: vision capture (local/remote_image) requires a display. On headless servers, use a virtual display (Xvfb) or run capture on a desktop/WSLg host and send images to the CAG OCR endpoint.

## Package → Host
1. Copy the tarball from `dist/`, e.g. `study-agents-YYYYMMDDHHMMSS.tar.gz`, to the target host:
   ```bash
   scp dist/study-agents-*.tar.gz user@host:/opt/
   ```
2. Extract and enter the project:
   ```bash
   cd /opt
   tar -xzf study-agents-*.tar.gz
   cd study-agents
   ```

Alternative (recommended for repeatable multi-host rollout):

```bash
python scripts/build_release_bundles.py
# Use dist/study-agents-backend-vps-<timestamp>.tar.gz on each VPS
```

## Configure Environment
1. Create your env file:
   ```bash
   cp .env.example .env
   ```
2. Choose Supabase mode:
   - Cloud Supabase: set hosted `SUPABASE_URL` + service-role `SUPABASE_KEY`.
   - Local Supabase Docker: run `./scripts/setup_local_supabase.sh` to start local Supabase stack and auto-populate `.env`.
3. Set required values:
   - `OPENAI_API_KEY` (and `OPENAI_EMBED_MODEL` if you want a different model)
   - `SUPABASE_URL`, `SUPABASE_KEY`
   - `OLLAMA_HOST`/`OLLAMA_API_KEY` if using Ollama cloud
   - Optional toggles: `USE_HYBRID_RETRIEVAL=true`, `RAG_USE_DOCLING=true`
4. Apply the schema to your Supabase target (cloud or local):
   ```bash
   psql "$SUPABASE_URL" < supabase_schema.sql
   ```
   or use the Supabase SQL console.

## Run with Docker Compose (recommended)
```bash
docker compose up -d --build
```
Services:
- `cag-service` (port 8000): `/cag-answer`, `/cag-ocr-answer`
- `rag-service` (port 8100): RAG builder API
- `utility-service`: base image for running CLI agents inside the container
- `copilot-service` (port 9010): PydanticAI backend for CopilotKit
- `scenario-service` (port 9000): Scenario API
- Vision capture: UI card and `/copilot/capture` endpoint are available; they need a display. For headless use, post images to `/cag-ocr-answer` or run capture on a GUI host.

Mounts: `.env`, `prompts/`, `data/`, `knowledge_graph/`, `research_output/` are bind-mounted so host edits are reflected live.

If you choose local Supabase mode, Supabase CLI runs an additional Docker stack (`supabase_db`, `supabase_rest`, `supabase_auth`, `supabase_storage`, `supabase_realtime`, `supabase_studio`, etc.) alongside these app services.

## Run Natively (venv)
```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .[full]
study-agents-validate --print-summary
```
Common CLIs:
- `study-agents-rag <pdf> --outdir out --push`
- `study-agents-cag --process <file>` (uses unified kg_pipeline ingestion)
- `study-agents-api` (HTTP API on port 8000)
- `study-agents-mcp` (MCP server over stdio)

## AWS Notes
- Use a security group allowing only the needed ports (8000/8100/9000) from trusted IPs or via an ALB/reverse proxy (TLS termination recommended).
- For EC2, install Docker + docker-compose-plugin (or use an AMI that already has them). The provided Dockerfiles include the `[full]` extra (Docling/OCR).
- Store secrets in SSM Parameter Store/Secrets Manager and template them into `.env` at boot (e.g., via cloud-init or a systemd drop-in).
- If using ECR, build and push images from this repo, then reference them in `docker-compose.yml` or your own ECS task definition.

## Validation
After configuration, run:
```bash
study-agents-validate --print-summary
```
Then smoke-test:
```bash
curl -X POST http://localhost:8000/cag-answer \
  -H "Content-Type: application/json" \
  -d '{"question": "Test connectivity"}'
```

Logs:
- Docker: `docker compose logs -f cag-service`
- Native: check `scenario_api.log`, `frontend_dev.log`, and per-agent stderr/stdout.

## Security Notes
- Prefer service-role key for backend runtime (`SUPABASE_KEY`); anon keys may limit write/ingestion paths.
- If running local Supabase, avoid exposing Supabase ports publicly; keep them localhost-bound unless proxied intentionally.
- Set `API_TOKEN` and `COPILOT_API_KEY` for production-facing deployments.
- `scenario-service` (`/scenarios*`) is not token-guarded by default in this split package; keep it private or enforce auth at the reverse proxy.
- Default compose ports are HTTP; for remote access terminate TLS (HTTPS) at a reverse proxy or load balancer.
- Clients can send auth as `X-API-Key` or `Authorization: Bearer <token>`.
