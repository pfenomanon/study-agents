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

Optional security:
- `API_TOKEN` (required by clients if set)

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
