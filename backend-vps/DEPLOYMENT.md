# Deployment Guide (Linux & AWS)

This repo can be moved to any Linux host (including AWS EC2) and run either natively or via Docker Compose.

## Prereqs
- Python 3.11+ (required only for native/venv deployment path)
- Docker + Docker Compose v2 (optional but recommended)
- Network access to OpenAI/Ollama (if used) and Supabase
- Ports to expose (typical): 443 (TLS gateway)
- Display note: vision capture (local/remote_image) requires a display. On headless servers, use a virtual display (Xvfb) or run capture on a desktop/WSLg host and send images to the CAG OCR endpoint.
- Service runtime versions are container-pinned:
  - Python APIs/builders: `python:3.11-slim`
  - Copilot frontend: `node:20-alpine`

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
   - If your Supabase endpoint is HTTPS with a private/self-signed cert, set `SUPABASE_HTTP_VERIFY=false` (or provide a trusted CA and keep it `true`)
   - Optional DB DSN for schema automation: `SUPABASE_DB_URL`
   - `OLLAMA_HOST`/`OLLAMA_API_KEY` if using Ollama cloud
   - `OLLAMA_REASON_MODEL` for default Ollama reasoning model (used when platform is `ollama` and no per-request model is supplied)
   - API auth tokens: `API_TOKEN`, `RAG_API_TOKEN`, `COPILOT_API_KEY`
   - Token-requirement defaults are enabled: `API_REQUIRE_TOKEN=true`, `RAG_REQUIRE_TOKEN=true`, `COPILOT_REQUIRE_TOKEN=true`
   - Gateway/Auth values: `PUBLIC_DOMAIN`, `ACME_EMAIL`, optional `GATEWAY_ALLOWED_CIDRS`
   - Authelia user mode: `AUTHELIA_USERS_SOURCE=file` (recommended) or `env`
   - Vault runtime values (non-dev flow): `VAULT_ADDR`, `VAULT_CACERT`, `VAULT_AUTH_METHOD=approle`, `VAULT_ROLE_ID_FILE`, `VAULT_SECRET_ID_FILE`
   - Vault hardening toggles: `ALLOW_PLAINTEXT_ENV_SECRETS=false`, `VAULT_SCRUB_ENV_SECRETS=true`
   - Vault UI OIDC client values: `AUTHELIA_VAULT_OIDC_CLIENT_ID`, `AUTHELIA_VAULT_OIDC_CLIENT_SECRET`, `AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI`
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
   - Recommended: open Supabase SQL Editor and run `supabase_schema.sql`.
   - Optional CLI method:
     - set `SUPABASE_DB_URL` in `.env` (Postgres DSN)
     - run:
       ```bash
       bash scripts/install_backend_vps.sh apply-schema
       ```

## E2E Encryption Requirements (Compose Path)

For full hop-by-hop encryption (client -> gateway -> service -> data dependencies):

1. Bootstrap internal TLS assets:
   ```bash
   bash scripts/bootstrap_internal_tls.sh
   ```
2. Bootstrap auth/gateway config (this also ensures TLS-backed Authelia config):
   ```bash
   bash scripts/bootstrap_authelia.sh
   ```
3. Validate Vault/OIDC gateway routes after auth/gateway bootstrap:
   ```bash
   bash scripts/validate_gateway_oidc_routes.sh <public-domain-or-ip>
   ```
4. Keep these set in `.env`:
   - `PUBLIC_DOMAIN`, `ACME_EMAIL`
   - `VAULT_ADDR_INTERNAL=https://vault:8200`
   - `VAULT_CACERT_INTERNAL=/tls/vault-ca.pem`
5. Do not expose direct service ports externally (`3000`, `8000`, `8100`, `9010`, `8200`). Expose only `443`.
6. Export and trust the local gateway CA on client devices when using local/internal PKI:
   ```bash
   bash scripts/export_caddy_root_ca.sh
   ```
   - Export now includes root + intermediate + chain files.
   - On Windows clients, remove old `Caddy Local Authority` certs from `Root`/`CA` stores before importing the current root+intermediate.
   - If `caddy-data` is recreated (new host, volume cleanup, compose project rename), repeat client trust import on every device.

## Run with Docker Compose (recommended)
Recommended orchestrated deploy (installs host deps, validates `.env`, starts stack, and runs smoke checks):
```bash
bash scripts/install_backend_vps.sh deploy
```

Direct compose path:
```bash
docker compose up -d --build
```

For x86_64 16GB edge hosts (for example ZimaBoard-class devices), use the tuned workflow:
```bash
bash scripts/install_zimaboard_16gb.sh start
```
This applies swap/sysctl host tuning, uses `docker-compose.zimaboard.yml`, and runs post-start validation.

Services:
- `cag-service` (port 8000, TLS listener): `/cag-answer`, `/cag-ocr-answer`
- `rag-service` (port 8100, TLS listener): RAG builder API
- `utility-service`: base image for running CLI agents inside the container
- `copilot-service` (port 9010, TLS listener): PydanticAI backend for CopilotKit
- `copilot-frontend` (port 3000, TLS endpoint): Next.js + CopilotKit UI (set `NEXT_PUBLIC_COPILOT_API`/`COPILOT_BACKEND_URL` if custom)
- `redis`: session + login-regulation state for gateway auth
- `authelia`: authentication portal/MFA and forward-auth backend
- `tls-gateway` (port 443): machine-terminated HTTPS endpoint for remote clients
- `vault` (port 8200 localhost bind, TLS): optional secret source for runtime env injection
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
- `docker/authelia/runtime/`: writable Authelia state (`db.sqlite3`, `notification.txt`)
- `AUTHELIA_VAULT_OIDC_CLIENT_SECRET`: Vault UI OIDC client secret (24-char alphanumeric)

Default behavior after bootstrap:
- `AUTHELIA_USERS_SOURCE=file` keeps user auth in `docker/authelia/users_database.yml` so plaintext passwords do not need to remain in `.env`
- Authelia runs unprivileged via `AUTHELIA_CONTAINER_UID`/`AUTHELIA_CONTAINER_GID` (auto-set on bootstrap when missing).
- Authelia mounts `configuration.yml`, `users_database.yml`, and `oidc_jwks_rs256.pem` read-only inside the container; only `docker/authelia/runtime/` is writable.
- Login username is in `AUTHELIA_AUTH_USERNAME` (defaults to `gateway-admin`)
- Login password is generated/stored in `AUTHELIA_AUTH_PASSWORD` only for first bootstrap or when `AUTHELIA_USERS_SOURCE=env`
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
  - Bootstrap also seeds a Vault admin client: `AUTHELIA_VAULT_OIDC_CLIENT_ID`, `AUTHELIA_VAULT_OIDC_CLIENT_SECRET`, `AUTHELIA_VAULT_OIDC_CLIENT_REDIRECT_URI`
- Gateway IP allowlist is enforced via `GATEWAY_ALLOWED_CIDRS`

Manage Authelia users without plaintext `.env` passwords:
```bash
bash scripts/authelia_user_manage.sh list
bash scripts/authelia_user_manage.sh add admin2 "Admin Two" admin2@local admins
bash scripts/authelia_user_manage.sh rotate-password gateway-admin
```

Bootstrap non-dev Vault (persistent raft + TLS + AppRole + OIDC admin login):
```bash
bash scripts/install_backend_vps.sh bootstrap-vault-nondev
```

This action:
- Enables Vault non-dev mode with raft persistence under `docker/vault/data`
- Initializes/unseals Vault and writes init material to `docker/vault/bootstrap/init.json`
- Creates `study-agents-runtime` read policy + AppRole credentials in `docker/vault/runtime/{role_id,secret_id}`
- Configures Vault OIDC auth using your existing Authelia IdP/gateway flow for Vault UI admin login
- Recreates auth gateway services and validates required Vault UI/OIDC popup routes
- Syncs non-placeholder `.env` secrets into `kv/study-agents/*` (runtime, OLLAMA, and Authelia secret material)
- Scrubs synced plaintext runtime/Ollama/Authelia secret values from `.env` for Vault-first runtime (backup saved to `docker/vault/bootstrap/env-pre-vault-scrub-<timestamp>.bak`)
- Recreates runtime services to consume Vault via AppRole (`VAULT_AUTH_METHOD=approle`)

Vault UI OIDC sign-in fields:
- Method: `OIDC`
- Role: `vault-admin`
- Mount path: `oidc`

If Vault login popup is blank/404 or fails to complete:
```bash
bash scripts/install_backend_vps.sh validate-gateway-oidc <public-domain-or-ip>
```

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
- Use a security group allowing only required ingress (typically 443) from trusted IPs.
- For EC2, install Docker + docker-compose-plugin (or use an AMI that already has them). The shared Python Dockerfile installs the `.[server]` profile (Docling OCR via RapidOCR/Tesseract, no EasyOCR by default).
- The shared Python Docker build pins CPU-only `torch`/`torchvision` wheels and fails if CUDA/NVIDIA Python packages appear.
- Store secrets in SSM Parameter Store/Secrets Manager and template them into `.env` at boot (e.g., via cloud-init or a systemd drop-in).
- If using ECR, build and push images from this repo, then reference them in `docker-compose.yml` or your own ECS task definition.

## Validation
After configuration, run:
```bash
study-agents-validate --print-summary
```
Then run stack TLS validation:
```bash
bash scripts/validate_backend_stack.sh
```
Validation now also verifies the gateway certificate chain (leaf + intermediate) and checks `/healthz` using the exported Caddy root trust.
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

Re-run stack validation checks at any time:
```bash
bash scripts/install_backend_vps.sh validate
```

Safe Docker cleanup (no data-volume deletion):
```bash
./scripts/docker_prune_safe.sh
```
