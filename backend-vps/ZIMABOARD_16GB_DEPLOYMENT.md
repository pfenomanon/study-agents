# ZimaBoard 2 (16GB) Deployment Guide

This guide is the full step-by-step path for ZimaBoard deployment with:
- local Supabase in Docker
- backend stack containers
- HTTPS LAN access through `tls-gateway`

## 0) One-command install/deploy path

```bash
cd /home/user1/study-agents/backend-vps
bash scripts/install_backend_vps.sh start-local-all
```

The installer handles Docker group session timing by using `sg docker` fallback for Docker-dependent helper scripts when needed.
Then bootstrap non-dev Vault:

```bash
bash scripts/install_backend_vps.sh bootstrap-vault-nondev
```

Then run LAN HTTPS setup:

```bash
bash scripts/install_backend_vps.sh configure-lan-https 10.72.72.161 10.72.72.0/24
bash scripts/install_backend_vps.sh validate-gateway-oidc 10.72.72.161
bash scripts/install_backend_vps.sh export-caddy-ca
```

## 1) Manual full path

### 1.1 Install host packages

```bash
sudo apt-get update -y
sudo apt-get install -y \
  docker.io docker-compose-v2 \
  curl jq ca-certificates python3 python3-yaml python3-venv \
  postgresql-client git openssl unzip
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Open a new shell.

### 1.2 Clone and enter repo

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
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
bash scripts/generate_local_api_keys.sh --write-env --overwrite
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/bootstrap_authelia.sh'
```

### 1.5 Setup local Supabase and write runtime env

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && PATH=$HOME/.local/bin:$PATH bash scripts/setup_local_supabase.sh'
```

This script installs/starts Supabase, then writes:
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SUPABASE_DB_URL`
- `SUPABASE_HTTP_VERIFY` when HTTPS/self-signed local API certs are detected

### 1.6 Apply schema to local DB

```bash
psql "$(awk -F= '/^SUPABASE_DB_URL=/{print $2}' .env)" -v ON_ERROR_STOP=1 -f supabase_schema.sql
```

### 1.7 Bootstrap non-dev Vault

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/bootstrap_vault_nondev.sh'
```

Vault bootstrap now applies Vault-first hardening:
- runtime secrets synced to Vault are scrubbed from `.env`
- backup is saved to `docker/vault/bootstrap/env-pre-vault-scrub-<timestamp>.bak`
- plaintext env fallback remains off unless `ALLOW_PLAINTEXT_ENV_SECRETS=true`

### 1.8 Start backend stack

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --build'
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --force-recreate cag-service rag-service copilot-service copilot-frontend tls-gateway authelia redis'
```

### 1.9 Validate

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/validate_zimaboard_stack.sh'
```

## 2) Configure LAN HTTPS gateway

Use this after backend is running.

```bash
bash scripts/configure_lan_https.sh 10.72.72.161 10.72.72.0/24
```

Validate Vault UI + OIDC popup routes:

```bash
bash scripts/install_backend_vps.sh validate-gateway-oidc 10.72.72.161
```

What it updates:
- `PUBLIC_DOMAIN`
- `AUTHELIA_OIDC_CLIENT_REDIRECT_URI`
- `GATEWAY_ALLOWED_CIDRS`
- regenerates Authelia config and recreates `authelia` + `tls-gateway`

Open from LAN clients:
- `https://10.72.72.161/`

Vault UI OIDC sign-in fields:
- Method: `OIDC`
- Role: `vault-admin`
- Mount path: `oidc`

## 3) Export and trust Caddy local CA

Export root CA cert from server:

```bash
bash scripts/export_caddy_root_ca.sh
```

Windows client import (PowerShell):

```powershell
scp user1@10.72.72.161:/home/user1/caddy-local-root.crt $env:USERPROFILE\Downloads\
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-root.crt" -CertStoreLocation Cert:\CurrentUser\Root
```

Restart browser and reopen `https://10.72.72.161/`.

## 4) URLs

- Frontend through gateway (LAN HTTPS): `https://<PUBLIC_DOMAIN>/`
- Frontend direct container bind (local host only): `http://127.0.0.1:3000/`
- Supabase local API: `http://127.0.0.1:54321`

## 5) Authelia one-time code during 2FA setup

This stack is configured with Authelia filesystem notifier by default, so one-time identity verification codes are saved in `/config/notification.txt` inside the Authelia container (not delivered by SMTP email).

When the browser prompts for a one-time code and no email is received:

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml exec -T authelia sh -lc "grep -E \"^[A-Z0-9]{8}$\" /config/notification.txt | tail -n 1"'
```

Important:
- Keep the Identity Verification dialog open while fetching/entering the code.
- If you click cancel/close, that code is invalidated; request a new code and re-run the command.

## 6) Operational commands

```bash
# status
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml ps'

# logs
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml logs -f cag-service rag-service copilot-service authelia tls-gateway'

# stop
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml down'
```

## 7) Recovery: `No space left on device` during build

If `pip install` or image build fails with `Errno 28`:

```bash
cd /home/user1/study-agents/backend-vps
bash scripts/install_backend_vps.sh reclaim-disk
bash scripts/install_backend_vps.sh restart
```
