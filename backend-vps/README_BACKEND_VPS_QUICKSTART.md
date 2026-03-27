# Backend-VPS Quickstart (Local Supabase + HTTPS Gateway)

## Fast path (scripted)

From `backend-vps/`:

```bash
bash scripts/install_backend_vps.sh start-local-all
```

This performs dependency install, local Supabase setup, schema apply, backend start, and validation.
If Docker group membership is not active in the current shell yet, the installer now auto-falls back to `sg docker` for Docker-dependent helper steps.
For non-dev secret management, run `bash scripts/install_backend_vps.sh bootstrap-vault-nondev` after the fast path completes.

## 1) Install dependencies

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

## 2) Get project

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
```

## 3) Prepare `.env`

```bash
cp -n .env.example .env
sed -i 's|^PUBLIC_DOMAIN=.*|PUBLIC_DOMAIN=127.0.0.1|' .env
sed -i 's|^ACME_EMAIL=.*|ACME_EMAIL=you@example.com|' .env
sed -i 's|^OPENAI_API_KEY=.*|OPENAI_API_KEY=sk-REPLACE_ME|' .env
sed -i 's|^COPILOT_SERVICE_WORKERS=.*|COPILOT_SERVICE_WORKERS=1|' .env
```

Replace:
- `OPENAI_API_KEY`
- `ACME_EMAIL`

## 4) Generate API tokens + auth config

```bash
bash scripts/generate_local_api_keys.sh --write-env --overwrite
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/bootstrap_authelia.sh'
```

## 5) Setup local Supabase and schema

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && PATH=$HOME/.local/bin:$PATH bash scripts/setup_local_supabase.sh'
psql "$(awk -F= '/^SUPABASE_DB_URL=/{print $2}' .env)" -v ON_ERROR_STOP=1 -f supabase_schema.sql
```

If the local Supabase API port is HTTPS-only, the script now writes:
- `SUPABASE_URL=https://...`
- `SUPABASE_HTTP_VERIFY=false` (self-signed local cert compatibility)

## 6) Bootstrap non-dev Vault (persistent + OIDC + AppRole)

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/bootstrap_vault_nondev.sh'
```

## 7) Start backend

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --build'
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --force-recreate cag-service rag-service copilot-service copilot-frontend tls-gateway authelia redis'
```

## 8) Validate

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/validate_zimaboard_stack.sh'
```

## 9) Enable LAN HTTPS on gateway (for other devices)

Use the Zima LAN IP or DNS name as the domain value.

```bash
bash scripts/configure_lan_https.sh 10.72.72.161 10.72.72.0/24
```

Equivalent wrapper action:

```bash
bash scripts/install_backend_vps.sh configure-lan-https 10.72.72.161 10.72.72.0/24
```

Open:
- `https://10.72.72.161/`

Note: this is gateway HTTPS. Do not use `https://<ip>:3000`.

## 10) Trust the local gateway CA cert on client devices

Export cert from server:

```bash
bash scripts/export_caddy_root_ca.sh
```

Equivalent wrapper action:

```bash
bash scripts/install_backend_vps.sh export-caddy-ca
```

Windows client import (PowerShell, run on the client):

```powershell
scp user1@10.72.72.161:/home/user1/caddy-local-root.crt $env:USERPROFILE\Downloads\
Import-Certificate -FilePath "$env:USERPROFILE\Downloads\caddy-local-root.crt" -CertStoreLocation Cert:\CurrentUser\Root
```

After import, restart browser and open `https://10.72.72.161/`.

## 11) Operations

```bash
# status
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml ps'

# logs
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml logs -f cag-service rag-service copilot-service tls-gateway'

# stop
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml down'
```

## 12) Authelia one-time code during 2FA setup

This deployment currently uses Authelia filesystem notifier (not SMTP email), so the one-time code is written to a file in the Authelia container.

If the browser shows Identity Verification and no email arrives:

```bash
sg docker -c 'cd /home/user1/study-agents/backend-vps && docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml exec -T authelia sh -lc "grep -E \"^[A-Z0-9]{8}$\" /config/notification.txt | tail -n 1"'
```

Important:
- Do not close/cancel the browser identity verification dialog before entering the code; closing invalidates that code.
- If the code expired, trigger a new code in the UI and run the command again.

## 13) If build fails with `No space left on device`

Run:

```bash
bash scripts/install_backend_vps.sh reclaim-disk
```

Then retry:

```bash
bash scripts/install_backend_vps.sh restart
```
