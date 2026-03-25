# Backend VPS Quickstart

Use this package on any new Linux VPS to run the backend services.

## 1) Extract on VPS

```bash
tar -xzf study-agents-backend-vps-20260311-025842.tar.gz
cd study-agents-backend-vps-20260311-025842
```

## 2) Install dependencies

```bash
bash scripts/install_backend_vps.sh deps
```

## 3) Configure environment

```bash
cp -n .env.example .env
nano .env
```

Minimum required keys in `.env`:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`

Recommended:
- Use a service-role key for `SUPABASE_KEY` (backend write/ingestion features).

Optional local Supabase mode (Docker):
```bash
./scripts/setup_local_supabase.sh
```
This starts the local Supabase container stack and updates `.env` with local credentials.

One-command local all-in-one startup (local Supabase + backend services + schema apply):
```bash
bash scripts/install_backend_vps.sh start-local-all
```

Optional security:
- `API_TOKEN` (required by clients if set)
- `RAG_API_TOKEN` (falls back to `API_TOKEN` if unset)
- `COPILOT_API_KEY` (required for `/copilot/*` if set)
- `SCENARIO_API_KEY` (falls back to `API_TOKEN` if unset)

Generate local backend service tokens (installer/admin generated):

```bash
# Recommended: URL-safe tokens, 32 random bytes each (~43 chars)
bash scripts/generate_local_api_keys.sh --write-env
```

Token compatibility guidance:
- Minimum entropy: 32 random bytes (256-bit) per key.
- Supported format: URL-safe (`A-Za-z0-9_-`) or hex.
- Script populates: `API_TOKEN`, `RAG_API_TOKEN`, `COPILOT_API_KEY`, `SCENARIO_API_KEY`.
- Keep service tokens distinct; avoid sharing one token across all services.

If you later enable TLS gateway auth (`./scripts/bootstrap_authelia.sh`), missing Authelia secrets are auto-generated and written into `.env`.

Transport security:
- Compose exposes HTTP by default.
- Put the APIs behind HTTPS (reverse proxy/load balancer) before internet exposure.

## 4) Start backend

```bash
bash scripts/install_backend_vps.sh start
```

For ZimaBoard/x86_64 16GB hosts, use the tuned workflow:

```bash
bash scripts/install_zimaboard_16gb.sh start
```

If you are using local Supabase and want one command for everything, use:
```bash
bash scripts/install_backend_vps.sh start-local-all
```

## 5) Verify and monitor

```bash
bash scripts/install_backend_vps.sh status
bash scripts/install_backend_vps.sh logs
```

Default endpoints:
- `POST /cag-answer` on port `8000`
- `POST /cag-ocr-answer` on port `8000`
- Copilot API on port `9010`

Auth header options:
- `X-API-Key: <token>`
- `Authorization: Bearer <token>`
