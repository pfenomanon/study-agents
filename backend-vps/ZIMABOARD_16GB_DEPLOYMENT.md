# ZimaBoard 16GB Deployment Guide

This guide is for running the `backend-vps` stack on a ZimaBoard-class x86_64 host with 16GB RAM using Docker Compose.

It assumes you are starting from a brand-new board with no operating system installed.

It adds:
- a Zima-specific compose override: `docker-compose.zimaboard.yml`
- a host prep/start script: `scripts/install_zimaboard_16gb.sh`
- a stack validation script: `scripts/validate_zimaboard_stack.sh`

## Scope and assumptions

- Fresh hardware is acceptable (no OS preinstalled).
- Target OS for this guide: Debian/Ubuntu Linux on x86_64.
- Hardware: 16GB RAM host (for example, ZimaBoard 2 1664)
- Storage: SSD preferred for `/var/lib/docker` and repo data
- Network: internet egress to OpenAI/Supabase and package repos

## 0) Install Linux OS (required on new board)

Perform these steps on a separate laptop/desktop first:

1. Download one of:
   - Debian 12 (amd64)
   - Ubuntu Server 22.04/24.04 LTS (amd64)
2. Flash the ISO to a USB drive (for example with Balena Etcher or Rufus).
3. Connect keyboard, monitor, ethernet, and the USB installer to the ZimaBoard.
4. Boot from USB and complete OS install:
   - hostname: choose a stable name (example: `zimaboard-study-agents`)
   - user: create a sudo-capable admin user
   - disk: install to the internal SSD/eMMC you want to run Docker on
   - network: DHCP is fine initially; static IP is optional
   - packages: include `OpenSSH server`
5. Reboot into the installed OS and log in.
6. Confirm baseline:

```bash
uname -m
cat /etc/os-release
sudo -v
```

Expected:
- `uname -m` returns `x86_64`
- OS is Debian/Ubuntu
- sudo works without errors

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

Token defaults (enabled by default):
- `API_REQUIRE_TOKEN=true`
- `RAG_REQUIRE_TOKEN=true`
- `COPILOT_REQUIRE_TOKEN=true`

Service token keys:
- `API_TOKEN`
- `RAG_API_TOKEN`
- `COPILOT_API_KEY`
- optional `SCENARIO_API_KEY` (if scenario API is enabled)

Generate local backend service tokens (optional; `start` auto-generates missing required values):

```bash
# Recommended: URL-safe tokens, 32 random bytes each
bash scripts/generate_local_api_keys.sh --write-env
```

Compatibility guidance:
- minimum entropy: 32 random bytes (256-bit) per key
- accepted practical formats: URL-safe token (~43 chars) or hex (64 chars)
- keep service keys distinct rather than reusing one token everywhere

Recommended memory setting for 16GB hosts:
- `COPILOT_SERVICE_WORKERS=1`

## 4) Apply Supabase schema

Use one of these methods:
- Supabase Cloud: run `supabase_schema.sql` in the Supabase SQL Editor.
- CLI/DSN path: set `SUPABASE_DB_URL` in `.env`, then run:

```bash
bash scripts/install_backend_vps.sh apply-schema
```

## 5) Start stack (build + run + validation)

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

## 6) Operational commands

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

## 7) Security gateway bootstrap (Authelia + TLS)

If you are exposing the service externally (not VPN-only), bootstrap gateway auth before production use:

```bash
./scripts/bootstrap_authelia.sh
bash scripts/install_zimaboard_16gb.sh restart
```

Required `.env` values before bootstrap:
- `PUBLIC_DOMAIN`
- `ACME_EMAIL`

If unset, bootstrap auto-generates:
- `AUTHELIA_AUTH_PASSWORD`, `AUTHELIA_OIDC_CLIENT_SECRET` (24-char alphanumeric)
- `AUTHELIA_SESSION_SECRET`, `AUTHELIA_STORAGE_ENCRYPTION_KEY`, `AUTHELIA_JWT_SECRET`, `AUTHELIA_OIDC_HMAC_SECRET` (64-char hex)
- `docker/authelia/oidc_jwks_rs256.pem` (RSA-2048 signing key)

## 8) Validation checklist

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

## 9) Recommended Zima workload profile

- Run backend stack directly on host Docker (no extra VM layer)
- Keep Docker data and bind mounts on SSD
- Leave swap enabled to absorb transient OCR/RAG spikes
- Enable optional `utility-service` and `vault` only when needed
- Keep remote access private (VPN/IP allowlists + API tokens); expose only port `443`.

## 10) Update procedure

```bash
cd /path/to/study-agents/backend-vps
git pull
bash scripts/install_zimaboard_16gb.sh start
```

## 11) Troubleshooting

- `docker permission denied` after `prepare`:
  - open a new shell session (docker group membership update).
- service not starting:
  - check logs: `bash scripts/install_zimaboard_16gb.sh logs <service>`
  - re-run validation: `bash scripts/validate_zimaboard_stack.sh`
- memory pressure:
  - confirm swap active: `swapon --show`
  - set `COPILOT_SERVICE_WORKERS=1`
  - stop optional profiles (`tools`, `vault`) when not required.
