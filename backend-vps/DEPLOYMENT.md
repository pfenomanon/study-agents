# Deployment Guide (Linux & AWS)

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
2. Set required values:
   - `OPENAI_API_KEY` (and `OPENAI_EMBED_MODEL` if you want a different model)
   - `SUPABASE_URL`, `SUPABASE_KEY`
   - `OLLAMA_HOST`/`OLLAMA_API_KEY` if using Ollama cloud
   - `OLLAMA_REASON_MODEL` for default Ollama reasoning model (used when platform is `ollama` and no per-request model is supplied)
   - API auth tokens: `API_TOKEN`, `RAG_API_TOKEN`, `COPILOT_API_KEY`
   - Gateway/Auth values: `PUBLIC_DOMAIN`, `ACME_EMAIL`, optional `GATEWAY_ALLOWED_CIDRS`
   - Optional toggles: `USE_HYBRID_RETRIEVAL=true`, `RAG_USE_DOCLING=true`
   - Optional security controls: request limits (`*_RATE_LIMIT_PER_MINUTE`), body limits (`COPILOT_MAX_BODY_BYTES`), path allowlists (`RAG_ALLOWED_*`, `COPILOT_ALLOWED_FILE_ROOTS`), and crawler SSRF guard (`WEB_RESEARCH_ALLOW_PRIVATE_NETWORKS=false`)
   - Generate local service tokens with:
     ```bash
     # 32 random bytes per key (recommended)
     bash scripts/generate_local_api_keys.sh --write-env
     ```
   - Token format guidance:
     - Use at least 32 random bytes (256-bit) per key.
     - Prefer URL-safe tokens (`A-Za-z0-9_-`, around 43 chars) or use 64-char hex.
     - Keep per-service keys distinct (`API_TOKEN`, `RAG_API_TOKEN`, `COPILOT_API_KEY`; optional `SCENARIO_API_KEY`).
3. Apply the schema to your Supabase project:
   ```bash
   psql "$SUPABASE_URL" < supabase_schema.sql
   ```
   or use the Supabase SQL console.

## Run with Docker Compose (recommended)
```bash
docker compose up -d --build
```

For x86_64 16GB edge hosts (for example ZimaBoard-class devices), use the tuned workflow:
```bash
bash scripts/install_zimaboard_16gb.sh start
```
This applies swap/sysctl host tuning, uses `docker-compose.zimaboard.yml`, and runs post-start validation.

Services:
- `cag-service` (port 8000): `/cag-answer`, `/cag-ocr-answer`
- `rag-service` (port 8100): RAG builder API
- `utility-service`: base image for running CLI agents inside the container
- `copilot-service` (port 9010): PydanticAI backend for CopilotKit
- `copilot-frontend` (port 3000): Next.js + CopilotKit UI (set `NEXT_PUBLIC_COPILOT_API`/`COPILOT_BACKEND_URL` if custom)
- `redis`: session + login-regulation state for gateway auth
- `authelia`: authentication portal/MFA and forward-auth backend
- `tls-gateway` (ports 80/443): machine-terminated HTTPS endpoint for remote clients
- Vision capture: UI card and `/copilot/capture` endpoint are available; they need a display. For headless use, post images to `/cag-ocr-answer` or run capture on a GUI host.

Build model:
- `cag-service` builds `study-agents-python` from `docker/python.Dockerfile`.
- `rag-service`, `copilot-service`, and `utility-service` reuse that same image with different entrypoints.

Mounts: `.env`, `prompts/`, `data/`, `knowledge_graph/`, `research_output/` are bind-mounted so host edits are reflected live.
For remote access, set `PUBLIC_DOMAIN` + `ACME_EMAIL` in `.env`, point DNS to the VPS, and use `https://<domain>/cag-ocr-answer`.

Bootstrap Authelia config and credentials before first secure start:
```bash
./scripts/bootstrap_authelia.sh
```

Authelia bootstrap generates missing local secrets automatically:
- `AUTHELIA_AUTH_PASSWORD` and `AUTHELIA_OIDC_CLIENT_SECRET`: 24-char alphanumeric
- `AUTHELIA_SESSION_SECRET`, `AUTHELIA_STORAGE_ENCRYPTION_KEY`, `AUTHELIA_JWT_SECRET`, `AUTHELIA_OIDC_HMAC_SECRET`: 64-char hex (32 random bytes each)
- `docker/authelia/oidc_jwks_rs256.pem`: RSA-2048 signing key

Default behavior after bootstrap:
- Login username is in `AUTHELIA_AUTH_USERNAME` (defaults to `gateway-admin`)
- Login password is generated/stored in `AUTHELIA_AUTH_PASSWORD` if not set
- Login regulation: 3 failed attempts then 20 minute cooldown (IP-based, to avoid user-target lockout abuse)
- MFA policy is enforced (`AUTHELIA_POLICY=two_factor`) with default second factor method `AUTHELIA_DEFAULT_2FA_METHOD=totp`
- Session policy defaults:
  - `AUTHELIA_SESSION_INACTIVITY=30 minutes`
  - `AUTHELIA_SESSION_EXPIRATION=3 hours`
  - `AUTHELIA_SESSION_REMEMBER_ME=1 week` (applies when user enables remember-me)
- Self-hosted IdP (OIDC) is enabled:
  - Discovery URL: `https://<PUBLIC_DOMAIN>/authelia/.well-known/openid-configuration`
  - Issuer in metadata: `https://<PUBLIC_DOMAIN>`
  - Gateway exposes OIDC endpoints without pre-auth redirect: `/.well-known/*`, `/jwks.json`, `/api/oidc/*`
  - Bootstrap creates an OIDC signing key at `docker/authelia/oidc_jwks_rs256.pem`
  - Bootstrap seeds one client from env: `AUTHELIA_OIDC_CLIENT_ID`, `AUTHELIA_OIDC_CLIENT_SECRET`, `AUTHELIA_OIDC_CLIENT_REDIRECT_URI`
- Gateway IP allowlist is enforced via `GATEWAY_ALLOWED_CIDRS`

## Run Natively (venv)
```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .[full]
study-agents-validate --print-summary
```
For slimmer installs, prefer:
- Server/API runtime: `pip install -e .[server]`
- Vision client host: `pip install -e .[vision-client]`
- Optional EasyOCR add-on (can pull torch): `pip install -e .[easyocr]`
Common CLIs:
- `study-agents-rag <pdf> --outdir out --push`
- `study-agents-cag --process <file>` (uses unified kg_pipeline ingestion)
- `study-agents-api` (HTTP API on port 8000)
- `study-agents-mcp` (MCP server over stdio)

## AWS Notes
- Use a security group allowing only the needed ports (8000/8100/9000) from trusted IPs or via an ALB/reverse proxy (TLS termination recommended).
- For EC2, install Docker + docker-compose-plugin (or use an AMI that already has them). The shared Python Dockerfile installs the `.[server]` profile (Docling OCR via RapidOCR/Tesseract, no EasyOCR by default).
- The shared Python Docker build pins CPU-only `torch`/`torchvision` wheels and fails if CUDA/NVIDIA Python packages appear.
- Store secrets in SSM Parameter Store/Secrets Manager and template them into `.env` at boot (e.g., via cloud-init or a systemd drop-in).
- If using ECR, build and push images from this repo, then reference them in `docker-compose.yml` or your own ECS task definition.

## Validation
After configuration, run:
```bash
study-agents-validate --print-summary
```
Then smoke-test:
```bash
curl -X POST https://<domain>/cag-answer \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"question": "Test connectivity"}'
```

Logs:
- Docker: `docker compose logs -f cag-service`
- Native: check `scenario_api.log`, `frontend_dev.log`, and per-agent stderr/stdout.

Safe Docker cleanup (no data-volume deletion):
```bash
./scripts/docker_prune_safe.sh
```
