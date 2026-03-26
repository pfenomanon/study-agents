# Backend VPS Quickstart

Use this package on a new Debian/Ubuntu VPS.

## 1) Extract on VPS

```bash
tar -xzf study-agents-backend-vps-<timestamp>.tar.gz
cd study-agents-backend-vps-<timestamp>
```

## 2) Install host dependencies

```bash
bash scripts/install_backend_vps.sh deps
```

This installs Docker Engine, Docker Compose plugin, and helper packages (`curl`, `jq`, `python3`, `python3-venv`, `postgresql-client`, `git`, `openssl`, `unzip`).

Runtime versions used by the services:
- Python service containers: `python:3.11-slim` (pinned in `docker/python.Dockerfile`)
- Copilot frontend container: `node:20-alpine` (pinned in `docker/copilot-frontend.Dockerfile`)

## 3) Configure `.env`

```bash
cp -n .env.example .env
nano .env
```

Required values:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY` (service-role key recommended)

Token defaults (important):
- `API_REQUIRE_TOKEN=true`
- `RAG_REQUIRE_TOKEN=true`
- `COPILOT_REQUIRE_TOKEN=true`

If required tokens are empty, the installer auto-generates and writes:
- `API_TOKEN`
- `RAG_API_TOKEN`
- `COPILOT_API_KEY`
- `SCENARIO_API_KEY`

Optional manual token generation:

```bash
bash scripts/generate_local_api_keys.sh --write-env
```

## 4) Apply Supabase schema

Cloud Supabase (recommended):
- Open Supabase SQL Editor for your project.
- Run `supabase_schema.sql`.

CLI path (optional):
- Set `SUPABASE_DB_URL` in `.env` (Postgres DSN), then run:

```bash
bash scripts/install_backend_vps.sh apply-schema
```

## 5) Start backend services

Recommended (build + start + health validation):
```bash
bash scripts/install_backend_vps.sh deploy
```

Equivalent manual start (without the full deploy wrapper):
```bash
bash scripts/install_backend_vps.sh start
```

## 6) Verify and monitor

```bash
bash scripts/install_backend_vps.sh status
bash scripts/install_backend_vps.sh logs
bash scripts/install_backend_vps.sh validate
```

## 7) Dummy-proof copy/paste flow (fresh host)

Run these exactly, in order:

```bash
# 0) enter project directory
cd /path/to/study-agents/backend-vps

# 1) install host dependencies + scaffold .env if missing
bash scripts/install_backend_vps.sh deps

# 2) open .env and fill required values (must not be placeholders)
nano .env

# 3) deploy and run smoke checks
bash scripts/install_backend_vps.sh deploy

# 4) confirm services are up
bash scripts/install_backend_vps.sh status
```

Success criteria after step 3:
- No `ERROR:` lines from the installer.
- `deploy` ends with `Validation complete: backend services are reachable.`
- `status` shows `cag-service`, `rag-service`, `copilot-service`, `copilot-frontend`, `redis`, `authelia`, `tls-gateway` as running.

Default local ports (localhost-bound):
- `127.0.0.1:8000` (`/cag-answer`, `/cag-ocr-answer`)
- `127.0.0.1:8100` (`/build`)
- `127.0.0.1:9010` (`/copilot/*`)
- `127.0.0.1:3000` (Copilot UI)

## Optional: local Supabase all-in-one mode

This mode starts local Supabase, applies `supabase_schema.sql`, then starts backend services:

```bash
bash scripts/install_backend_vps.sh start-local-all
```

## Optional: ZimaBoard 2 / x86_64 16GB tuned path

```bash
bash scripts/install_zimaboard_16gb.sh start
```

See `ZIMABOARD_16GB_DEPLOYMENT.md` for full preflight, tuning, and operations guidance.
