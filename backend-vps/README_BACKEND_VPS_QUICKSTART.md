# Backend-VPS Quickstart (Local Supabase + HTTPS Gateway)

## Fast path (scripted)

From `backend-vps/`:

```bash
bash scripts/install_backend_vps.sh start-local-all
```

This performs dependency install, internal TLS bootstrap, local Supabase setup, schema apply, backend start, and validation.
If Docker group membership is not active in the current shell yet, the installer now auto-falls back to `sg docker` for Docker-dependent helper steps.

## 1) Install dependencies

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

## 2) Get project

```bash
git clone git@github.com:pfenomanon/study-agents.git
cd study-agents/backend-vps
PROJECT_ROOT="$(pwd)"
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

## 4) Generate internal TLS assets, API tokens, and auth config

```bash
bash scripts/bootstrap_internal_tls.sh
bash scripts/generate_local_api_keys.sh --write-env --overwrite
bash scripts/bootstrap_authelia.sh
```

## 5) Setup local Supabase and schema

```bash
bash scripts/setup_local_supabase.sh
psql "$(awk -F= '/^SUPABASE_DB_URL=/{print $2}' .env)" -v ON_ERROR_STOP=1 -f supabase_schema.sql
```

## 6) Start backend

```bash
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml up -d --force-recreate cag-service rag-service copilot-service copilot-frontend tls-gateway authelia redis vault
```

## 7) Validate

```bash
bash scripts/validate_zimaboard_stack.sh
bash scripts/validate_backend_stack.sh
```

## 8) Enable LAN HTTPS on gateway (for other devices)

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

## 9) Bootstrap non-dev Vault secret injection + OIDC admin login

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

## 10) Trust the local gateway CA cert on client devices

Export certs from server:

```bash
bash scripts/export_caddy_root_ca.sh
```

Equivalent wrapper action:

```bash
bash scripts/install_backend_vps.sh export-caddy-ca
```

This exports:
- `$HOME/caddy-local-root.crt`
- `$HOME/caddy-local-intermediate.crt`
- `$HOME/caddy-local-chain.crt`

Windows client import (PowerShell, run on the client as Administrator):

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

After import, restart browser and open `https://10.72.72.161/`.
Any time `caddy-data` is recreated (or Caddy local CA rotates), repeat this trust step on every client.

## 11) E2E encryption checks (quick)

```bash
cd "$PROJECT_ROOT"
CA=docker/internal-tls/internal-ca.crt
VAULT_CA=docker/internal-tls/vault-ca.pem

# Internal service TLS
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8000/cag-answer -H 'content-type: application/json' --data '{"question":"health check"}'
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8100/build -H 'content-type: application/json' --data '{}'
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:9010/copilot/chat -H 'content-type: application/json' --data '{}'
curl --cacert "$CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:3000/

# Vault TLS (host) and plaintext rejection
curl --cacert "$VAULT_CA" -sS -o /dev/null -w '%{http_code}\n' https://127.0.0.1:8200/v1/sys/health
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8200/v1/sys/health

# Gateway chain/trust (uses exported Caddy root)
GATEWAY_CA="$HOME/caddy-local-root.crt"
curl --cacert "$GATEWAY_CA" --resolve 10.72.72.161:443:127.0.0.1 -sS -o /dev/null -w '%{http_code}\n' https://10.72.72.161/healthz
```

Expected:
- service checks return acceptable app statuses (for example `200/400/401/403/422`)
- Vault HTTPS returns `200` (or another valid health code like `429/472/473/501/503`)
- Vault plaintext HTTP returns `400`

Security posture:
- Expose only gateway `443` to remote clients.
- Keep direct service ports (`3000`, `8000`, `8100`, `9010`, `8200`) bound to loopback/private only.

## 12) Operations

```bash
# status
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml ps

# logs
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml logs -f cag-service rag-service copilot-service tls-gateway

# stop
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml down
```

## 13) Authelia one-time code during 2FA setup

This deployment currently uses Authelia filesystem notifier (not SMTP email), so the one-time code is written to a file in the Authelia container.

If the browser shows Identity Verification and no email arrives:

```bash
docker compose -f docker-compose.yml -f docker-compose.zimaboard.yml exec -T authelia sh -lc "grep -E \"^[A-Z0-9]{8}$\" /config/notification.txt | tail -n 1"
```

Important:
- Do not close/cancel the browser identity verification dialog before entering the code; closing invalidates that code.
- If the code expired, trigger a new code in the UI and run the command again.

## 14) Rotate Authelia admin credentials securely (no plaintext `.env` password)

```bash
cd "$PROJECT_ROOT"
bash scripts/authelia_user_manage.sh rotate-password gateway-admin
```

To add a separate break-glass admin account:

```bash
cd "$PROJECT_ROOT"
bash scripts/authelia_user_manage.sh add gateway-admin-2 --display-name "Gateway Admin 2" --email admin2@local.invalid --groups admins
```

## 15) If build fails with `No space left on device`

Run:

```bash
bash scripts/install_backend_vps.sh reclaim-disk
```

Then retry:

```bash
bash scripts/install_backend_vps.sh restart
```
