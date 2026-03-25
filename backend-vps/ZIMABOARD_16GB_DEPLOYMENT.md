# ZimaBoard 16GB Deployment Guide

This guide is for running the `backend-vps` stack on a ZimaBoard-class x86_64 host with 16GB RAM using Docker Compose.

It adds:
- a Zima-specific compose override: `docker-compose.zimaboard.yml`
- a host prep/start script: `scripts/install_zimaboard_16gb.sh`
- a stack validation script: `scripts/validate_zimaboard_stack.sh`

## Scope and assumptions

- Host OS: Debian/Ubuntu Linux on x86_64
- Hardware: 16GB RAM host (for example, ZimaBoard 2 1664)
- Storage: SSD preferred for `/var/lib/docker` and repo data
- Network: internet egress to OpenAI/Supabase and package repos

## 1) Clone and enter backend path

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
```

## 2) Host preparation (one command)

This action installs Docker/Compose if missing, provisions swap, applies sysctl tuning, and creates `.env` if absent.

```bash
bash scripts/install_zimaboard_16gb.sh prepare
```

Default host tuning performed:
- swap file: `/swapfile-study-agents` (8GB)
- sysctl file: `/etc/sysctl.d/99-study-agents-zimaboard.conf`

Optional overrides:

```bash
SWAP_SIZE_GB=6 MIN_FREE_DISK_GB=20 bash scripts/install_zimaboard_16gb.sh prepare
```

## 3) Configure `.env`

Populate required keys in `backend-vps/.env`:
- `OPENAI_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY` (service role key recommended for backend writes/ingestion)

Recommended security keys:
- `API_TOKEN`
- `RAG_API_TOKEN`
- `COPILOT_API_KEY`

Recommended memory setting for 16GB hosts:
- `COPILOT_SERVICE_WORKERS=1`

## 4) Start stack (build + run + validation)

```bash
bash scripts/install_zimaboard_16gb.sh start
```

What this does:
- validates required `.env` keys
- validates merged compose config (`docker-compose.yml` + `docker-compose.zimaboard.yml`)
- starts services with rebuild
- runs `scripts/validate_zimaboard_stack.sh`

Default expected running services:
- `cag-service`
- `rag-service`
- `copilot-service`
- `copilot-frontend`
- `redis`
- `authelia`
- `tls-gateway`

Optional services are kept off by default on 16GB:
- `utility-service` profile `tools`
- `vault` profile `vault`

Start optional services only when needed:

```bash
bash scripts/install_zimaboard_16gb.sh start-tools
bash scripts/install_zimaboard_16gb.sh start-vault
```

## 5) Operational commands

```bash
# show status
bash scripts/install_zimaboard_16gb.sh status

# tail specific logs (default is cag-service)
bash scripts/install_zimaboard_16gb.sh logs cag-service

# run validation checks again
bash scripts/install_zimaboard_16gb.sh validate

# stop stack
bash scripts/install_zimaboard_16gb.sh stop
```

## 6) Security gateway bootstrap (Authelia + TLS)

If you are exposing the service externally (not VPN-only), bootstrap gateway auth before production use:

```bash
./scripts/bootstrap_authelia.sh
bash scripts/install_zimaboard_16gb.sh restart
```

Required `.env` values before bootstrap:
- `PUBLIC_DOMAIN`
- `ACME_EMAIL`

## 7) Validation checklist

Run:

```bash
bash scripts/validate_zimaboard_stack.sh
```

Validation script checks:
- compose file integrity
- required services in running state
- HTTP reachability checks for:
  - `http://127.0.0.1:8000/cag-answer`
  - `http://127.0.0.1:8100/build`
  - `http://127.0.0.1:9010/copilot/chat`
  - `http://127.0.0.1:3000/`

## 8) Recommended Zima workload profile

- Run backend stack directly on host Docker (no extra VM layer)
- Keep Docker data and bind mounts on SSD
- Leave swap enabled to absorb transient OCR/RAG spikes
- Enable optional `utility-service` and `vault` only when needed
- Keep remote access private (VPN/IP allowlists + API tokens)

## 9) Update procedure

```bash
cd /path/to/study-agents/backend-vps
git pull
bash scripts/install_zimaboard_16gb.sh start
```

## 10) Troubleshooting

- `docker permission denied` after `prepare`:
  - open a new shell session (docker group membership update).
- service not starting:
  - check logs: `bash scripts/install_zimaboard_16gb.sh logs <service>`
  - re-run validation: `bash scripts/validate_zimaboard_stack.sh`
- memory pressure:
  - confirm swap active: `swapon --show`
  - set `COPILOT_SERVICE_WORKERS=1`
  - stop optional profiles (`tools`, `vault`) when not required.
