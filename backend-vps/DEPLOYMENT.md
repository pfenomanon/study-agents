# Deployment Guide (Linux & AWS)

For clone/pull onboarding in this split deployment repo, start with `../GETTING_STARTED.md`.

This repo can be moved to any Linux host (including AWS EC2) and run either natively or via Docker Compose.

## Prereqs
- Python 3.11+
- Docker + Docker Compose v2 (optional but recommended)
- Network access to OpenAI/Ollama (if used) and Supabase
- Ports to expose (typical): 8000 (CAG API), 8100 (RAG service), 9010 (Copilot API)
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
- Vision capture: UI card and `/copilot/capture` endpoint are available; they need a display. For headless use, post images to `/cag-ocr-answer` or run capture on a GUI host.

For local self-hosted Supabase + app stack in one command:
```bash
bash scripts/install_backend_vps.sh start-local-all
```
This runs local Supabase, applies `supabase_schema.sql`, then starts the backend app services.

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

## AWS EC2 Step-by-Step (Ubuntu 24.04, 16 GB RAM, 200 GB Disk)

Use this when you want AWS to match your current Hostinger VPS profile.

1. Create the EC2 instance
   ```text
   AMI: Ubuntu Server 24.04 LTS (x86_64)
   Instance type: t3.xlarge (4 vCPU / 16 GiB RAM) or equivalent
   Root volume: 200 GiB gp3
   ```
2. Configure networking
   - Attach an Elastic IP so the public IP remains stable.
   - Security Group inbound rules:
     - `22/tcp` from your admin IP only
     - `443/tcp` from client ranges (or `0.0.0.0/0` if public)
     - Optional temporary/debug ports: `8000`, `8100`, `9010` from your admin IP only
   - Keep Supabase local ports (`54321+`) closed to the public internet.
3. SSH to the server
   ```bash
   ssh -i /path/to/key.pem ubuntu@<EC2_PUBLIC_IP>
   ```
4. Install git and clone
   ```bash
   sudo apt-get update -y
   sudo apt-get install -y git
   cd /home/ubuntu
   git clone git@github.com:pfenomanon/study-agents.git
   cd study-agents/backend-vps
   ```
5. Configure environment
   ```bash
   cp .env.example .env
   nano .env
   ```
   Required values:
   - `OPENAI_API_KEY`
   - `SUPABASE_URL`, `SUPABASE_KEY` (cloud mode) OR run local Supabase (next step)
   - Any auth/API secrets you use (`API_TOKEN`, `COPILOT_API_KEY`, etc.)
6. Choose Supabase mode
   - Cloud Supabase: keep your hosted `SUPABASE_URL` and service-role `SUPABASE_KEY`.
   - Local Supabase in Docker:
     ```bash
     chmod +x scripts/setup_local_supabase.sh
     ./scripts/setup_local_supabase.sh
     ```
     This runs `supabase start` and updates `.env` with local URL/key.
7. Start the app stack
   ```bash
   docker compose up -d --build
   ```
8. Verify containers
   ```bash
   docker compose ps
   docker ps --format 'table {{.Names}}\t{{.Status}}'
   ```
9. Smoke test APIs from the server
   ```bash
   curl -sS http://127.0.0.1:8000/health || true
   curl -sS http://127.0.0.1:9010/docs >/dev/null && echo "copilot up"
   ```
10. Optional: run through TLS gateway only
    - Point DNS to the EC2 Elastic IP.
    - Use `tls-gateway` as the public entrypoint on `443`.
    - Keep direct app ports (`8000/8100/9010`) private.

### AWS Ops Notes
- Put secrets in AWS SSM Parameter Store or Secrets Manager, then render `.env` during bootstrap.
- For automated startup on reboot, add a systemd service that runs `docker compose up -d` in `backend-vps/`.
- If you move to ECR/ECS later, build/push these images and reuse the same env/secrets model.

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
- Native: check `frontend_dev.log` and per-agent stderr/stdout.

## Security Notes
- Prefer service-role key for backend runtime (`SUPABASE_KEY`); anon keys may limit write/ingestion paths.
- If running local Supabase, avoid exposing Supabase ports publicly; keep them localhost-bound unless proxied intentionally.
- Set `API_TOKEN` and `COPILOT_API_KEY` for production-facing deployments.
- Default compose ports are HTTP; for remote access terminate TLS (HTTPS) at a reverse proxy or load balancer.
- Clients can send auth as `X-API-Key` or `Authorization: Bearer <token>`.
