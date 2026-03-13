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

Optional security:
- `API_TOKEN` (required by clients if set)
- `COPILOT_API_KEY` (required for `/copilot/*` if set)

Transport security:
- Compose exposes HTTP by default.
- Put the APIs behind HTTPS (reverse proxy/load balancer) before internet exposure.

## 4) Start backend

```bash
bash scripts/install_backend_vps.sh start
```

## 5) Verify and monitor

```bash
bash scripts/install_backend_vps.sh status
bash scripts/install_backend_vps.sh logs
```

Default endpoints:
- `POST /cag-answer` on port `8000`
- `POST /cag-ocr-answer` on port `8000`
- Scenario API on port `9000`
- Copilot API on port `9010`

Auth header options:
- `X-API-Key: <token>`
- `Authorization: Bearer <token>`
