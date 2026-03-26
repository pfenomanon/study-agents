# Study Agents Deployment Repo

This repository is split into two folders:

- `backend-vps/`: Linux VPS backend host package (APIs/services)
- `local-run/`: local client package (Windows/local runtime)

## Important Path Note

- For backend deployment, always run commands from `study-agents/backend-vps`.
- `local-run/` is for client-side capture workflows only.

Backend quickstart:

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
cp .env.example .env
bash scripts/generate_local_api_keys.sh --write-env
# Apply supabase_schema.sql in Supabase SQL Editor.
# Optional CLI path (needs SUPABASE_DB_URL in .env):
# bash scripts/install_backend_vps.sh apply-schema
docker compose up -d --build
```

Optional root helper:

```bash
cd study-agents
./bootstrap.sh
```

Not included in Git (by design):

- Sensitive `.env` values
- Runtime/generated state (`data/`, `knowledge_graph/`, `research_output/`, Authelia runtime secrets/state)

Start here:
- `GETTING_STARTED.md` (authoritative clone/pull install + personalization guide)
- `SETUP.md` (concise setup path with path guardrails)

Additional quick references:
- Backend host quickstart: `backend-vps/README_BACKEND_VPS_QUICKSTART.md`
- ZimaBoard 16GB deployment + operations: `backend-vps/ZIMABOARD_16GB_DEPLOYMENT.md`
- Windows client quickstart: `local-run/README_WINDOWS_CLIENT_QUICKSTART.md`
- macOS client quickstart: `local-run/README_MAC_CLIENT_QUICKSTART.md`
- Native no-Python remote capture scripts: `local-run/native/README.md`
- Generated deployment quickstart artifact: `DEPLOYMENT-QUICKSTART-20260311-025842.md`
