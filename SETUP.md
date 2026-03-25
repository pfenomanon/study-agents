# Setup Quickstart

Use this when you want the fastest reliable path without reading the full guide.

## 1) Clone

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents
```

## 2) Enter the correct backend path

```bash
cd backend-vps
```

Important:
- Backend deploy/service commands run in `backend-vps/`.
- `local-run/` is for Windows/macOS client capture workflows.

## 3) Create environment file

```bash
cp .env.example .env
```

Set at minimum:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`

Generate local backend service tokens (recommended: 32-byte URL-safe keys):

```bash
bash scripts/generate_local_api_keys.sh --write-env
```

This generates `API_TOKEN`, `RAG_API_TOKEN`, `COPILOT_API_KEY`, and `SCENARIO_API_KEY`.

## 4) Start backend stack

```bash
docker compose up -d --build
```

ZimaBoard/x86_64 16GB tuned path:

```bash
bash scripts/install_zimaboard_16gb.sh start
```

## 5) Optional root helper

From repo root:

```bash
./bootstrap.sh
```

This helper changes into `backend-vps`, creates `.env` from `.env.example` if missing, and prints next commands.

## Not in Git (by design)

- Sensitive values in `.env`
- Runtime/generated data and state:
  - `backend-vps/data/`
  - `backend-vps/knowledge_graph/`
  - `backend-vps/research_output/`
  - `backend-vps/docker/authelia/`
