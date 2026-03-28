# ZimaBoard 2 (16GB) Deployment Guide

This guide is the full step-by-step path for ZimaBoard deployment with:
- local Supabase in Docker
- backend stack containers
- HTTPS LAN access through `tls-gateway`
- internal hop-by-hop TLS for CAG/RAG/Copilot/frontend + Vault

## 0) One-command install/deploy path

```bash
cd /path/to/study-agents/backend-vps
bash scripts/install_backend_vps.sh start-local-all
```

The installer handles Docker group session timing by using `sg docker` fallback for Docker-dependent helper scripts when needed.

Then run LAN HTTPS setup:

```bash
bash scripts/install_backend_vps.sh configure-lan-https 10.72.72.161 10.72.72.0/24
bash scripts/install_backend_vps.sh export-caddy-ca
```

## 1) Manual full path

### 1.1 Install host packages

```bash
sudo apt-get update -y
sudo apt-get install -y \
  docker.io docker-compose-plugin \
  curl jq ca-certificates python3 python3-venv \
  postgresql-client git openssl unzip
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Open a new shell.

### 1.2 Clone and enter repo

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
PROJECT_ROOT="$(pwd)"
```

### 1.3 Initialize `.env`

```bash
cp -n .env.example .env
sed -i 's|^PUBLIC_DOMAIN=.*|PUBLIC_DOMAIN=127.0.0.1|' .env
sed -i 's|^ACME_EMAIL=.*|ACME_EMAIL=you@example.com|' .env
sed -i 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY=sk-REPLACE_ME|' .env
sed -i 's|^COPILOT_SERVICE_WORKERS=.*|COPILOT_SERVICE_WORKERS=1|' .env
```

Set real values for:
- `OPENAI_API_KEY`
- `ACME_EMAIL`

### 1.4 Generate API keys and bootstrap auth

```bash
bash scripts/bootstrap_internal_tls.sh
bash scripts/generate_local_api_keys.sh --write-env --overwrite
bash scripts/bootstrap_authelia.sh
```

### 1.5 Setup local Supabase and write runtime env

```bash
bash scripts/setup_local_supabase.sh
```

This script installs/starts Supabase, then writes:
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SUPABASE_DB_URL`

### 1.6 Apply schema to local DB

```bash
psql "$(awk -F= '/^SUPABASE_DB_URL=/{print $2}' .env)" -v ON_ERROR_STOP=1 -f supabase_schema.sql
```

### 1.7 Start backend stack

```bash
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --force-recreate cag-service rag-service copilot-service copilot-frontend tls-gateway authelia redis vault
```

### 1.8 Validate

```bash
bash scripts/validate_zimaboard_stack.sh
bash scripts/validate_backend_stack.sh
```

## 2) Configure LAN HTTPS gateway

Use this after backend is running.

```bash
bash scripts/configure_lan_https.sh 10.72.72.161 10.72.72.0/24
```

What it updates:
- `PUBLIC_DOMAIN`
- `AUTHELIA_OIDC_CLIENT_REDIRECT_URI`
- `GATEWAY_ALLOWED_CIDRS`
- regenerates Authelia config and recreates `authelia` + `tls-gateway`

Open from LAN clients:
- `https://10.72.72.161/`

## 3) Export and trust Caddy local CA

Export root CA cert from server:

```bash
bash scripts/export_caddy_root_ca.sh
```

This exports:
- `$HOME/caddy-local-root.crt`
- `$HOME/caddy-local-intermediate.crt`
- `$HOME/caddy-local-chain.crt`

Windows client import (PowerShell, run as Administrator):

```powershell
scp <ssh-user>@10.72.72.161:/home/<ssh-user>/caddy-local-root.crt $env:USERPROFILE\Downloads\
scp <ssh-user>@10.72.72.161:/home/<ssh-user>/caddy-local-intermediate.crt $env:USERPROFILE\Downloads\

$stores = @(
  'Cert:\CurrentUser\Root', 'Cert:\CurrentUser\CA',
  'Cert:\LocalMachine\Root', 'Cert:\LocalMachine\CA'
)
foreach ($store in $stores) {
  Get-ChildItem $store | Where-Object { $_.Subject -like '*Caddy Local Authority*' } | Remove-Item -Force
}

Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-root.crt" -CertStoreLocation 'Cert:\CurrentUser\Root'
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-intermediate.crt" -CertStoreLocation 'Cert:\CurrentUser\CA'
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-root.crt" -CertStoreLocation 'Cert:\LocalMachine\Root'
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-intermediate.crt" -CertStoreLocation 'Cert:\LocalMachine\CA'
```

Restart browser and reopen `https://10.72.72.161/`.
Any time `caddy-data` is recreated (or Caddy local CA rotates), repeat this trust step on every client.

## 4) Bootstrap non-dev Vault secret injection + OIDC admin login

```bash
cd "$PROJECT_ROOT"
bash scripts/bootstrap_vault_nondev.sh
```

What this configures:
- persistent non-dev Vault (Raft + TLS)
- AppRole runtime auth for backend services (`role_id`/`secret_id` file mounts)
- secret sync from `.env` into `kv/study-agents/*`
- Vault OIDC admin role via existing Authelia IdP flow

Admin login:

```bash
vault login -method=oidc -address=https://127.0.0.1:8200 role=vault-admin
```

Vault UI:
- `https://127.0.0.1:8200/ui/` (use SSH tunnel from remote clients)

## 5) URLs

- Frontend through gateway (LAN HTTPS): `https://<PUBLIC_DOMAIN>/`
- Frontend direct container bind (localhost only, internal cert): `https://127.0.0.1:3000/`
- Supabase local API (TLS): `https://127.0.0.1:54321`

Plaintext checks:
- `http://127.0.0.1:8200` (Vault) is expected to fail with HTTP `400` because Vault is TLS-only.
- `http://127.0.0.1:54321` (Supabase API) is expected to fail with HTTP `400`.

## 5.1) E2E encryption verification commands

```bash
cd "$PROJECT_ROOT"
CA=docker/internal-tls/internal-ca.crt
VAULT_CA=docker/internal-tls/vault-ca.pem

curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8000/cag-answer -H 'content-type: application/json' --data '{"question":"health check"}'
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8100/build -H 'content-type: application/json' --data '{}'
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:9010/copilot/chat -H 'content-type: application/json' --data '{}'
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:3000/
curl --cacert "$VAULT_CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8200/v1/sys/health
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8200/v1/sys/health
GATEWAY_CA="$HOME/caddy-local-root.crt"
curl --cacert "$GATEWAY_CA" --resolve 10.72.72.161:443:127.0.0.1 -sS -o /dev/null -w '%{http_code}\n' https://10.72.72.161/healthz
```

Expected:
- Service calls return acceptable app-level statuses.
- Vault HTTPS returns `200` (or another valid health code).
- Vault plaintext returns `400`.

Exposure rule:
- Publish only `443` to LAN/WAN clients.
- Keep direct service ports (`3000`, `8000`, `8100`, `9010`, `8200`) local/private.

## 6) Authelia one-time code during 2FA setup

This stack is configured with Authelia filesystem notifier by default, so one-time identity verification codes are saved in `/config/notification.txt` inside the Authelia container (not delivered by SMTP email).

When the browser prompts for a one-time code and no email is received:

```bash
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml exec -T authelia sh -lc "grep -E \"^[A-Z0-9]{8}$\" /config/notification.txt | tail -n 1"
```

Important:
- Keep the Identity Verification dialog open while fetching/entering the code.
- If you click cancel/close, that code is invalidated; request a new code and re-run the command.

## 7) Operational commands

```bash
# status
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml ps

# logs
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml logs -f cag-service rag-service copilot-service authelia tls-gateway

# stop
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml down
```

## 8) Rotate Authelia admin credentials securely (no plaintext `.env` password)

```bash
cd "$PROJECT_ROOT"
bash scripts/authelia_user_manage.sh rotate-password gateway-admin
```

To add a second admin:

```bash
cd "$PROJECT_ROOT"
bash scripts/authelia_user_manage.sh add gateway-admin-2 --display-name "Gateway Admin 2" --email admin2@local.invalid --groups admins
```

## 9) Recovery: `No space left on device` during build

If `pip install` or image build fails with `Errno 28`:

```bash
cd "$PROJECT_ROOT"
bash scripts/install_backend_vps.sh reclaim-disk
bash scripts/install_backend_vps.sh restart
```
