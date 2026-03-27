# Backend-VPS Deployment Run Log (Audit + Debug Handoff)

## 1) Deployment Metadata (Audit)

- Deployment date (UTC): **2026-03-26**
- Host role/name observed: `study-agents-backend`
- OS: Ubuntu 24.04.3 LTS
- Architecture: x86_64
- RAM detected during run: 7685 MB
- Root filesystem at start: 13G total (`/dev/mapper/ubuntu--vg-ubuntu--lv`)
- Repo path: `/home/user1/study-agents/backend-vps`

## 2) Executive Status (Fast Handoff)

- Final result: **successful deploy**
- Backend stack: **healthy**
- Local Supabase: **running in Docker** (minimal active profile)
- Schema: `supabase_schema.sql` **applied to local Supabase DB**
- Runtime wiring: backend containers confirmed using local Supabase

Final validation outputs:
- `cag-service`: HTTP 200
- `rag-service`: HTTP 400
- `copilot-service`: HTTP 422
- `copilot-frontend`: HTTP 200

## 3) Final Known-Good Runtime State (Audit)

### 3.1 `.env` keys (effective)

- `SUPABASE_URL=http://172.17.0.1:54321`
- `SUPABASE_DB_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres`
- `SUPABASE_KEY=<service_role_jwt>`
- `COPILOT_REQUIRE_PROFILE_SCHEMA=true`
- `PUBLIC_DOMAIN=127.0.0.1`

### 3.2 Running containers at close

Backend:
- `backend-vps-cag-service-1`
- `backend-vps-rag-service-1`
- `backend-vps-copilot-service-1`
- `backend-vps-copilot-frontend-1`
- `backend-vps-redis-1`
- `backend-vps-authelia-1` (healthy)
- `backend-vps-tls-gateway-1`

Local Supabase (active):
- `supabase_db_backend-vps` (healthy)
- `supabase_kong_backend-vps` (healthy)
- `supabase_rest_backend-vps`
- `supabase_auth_backend-vps` (healthy)

### 3.3 Verification performed

- Validator: `bash scripts/validate_zimaboard_stack.sh` (pass)
- In-container check from `cag-service`:
  - `SUPABASE_URL` present as `http://172.17.0.1:54321`
  - Supabase REST call returned HTTP 200

## 4) Chronological Execution Log (Audit)

1. Attempted `scripts/install_zimaboard_16gb.sh prepare`.
2. Attempted `scripts/install_backend_vps.sh deps`.
3. Installed dependencies manually (`docker.io`, `docker-compose-v2`, etc.).
4. Initialized `.env`, generated API keys, bootstrapped Authelia.
5. Brought up backend compose stack.
6. Switched deployment to local Supabase requirement.
7. Attempted `scripts/setup_local_supabase.sh` (CLI install URL failure).
8. Installed Supabase CLI from GitHub release tarball.
9. Started Supabase; hit disk pressure (`No space left on device`).
10. Reclaimed disk and retried.
11. Ran `supabase init --yes` to create project config.
12. Started local Supabase, captured env outputs.
13. Set `.env` to local Supabase values.
14. Applied `supabase_schema.sql` to local DB.
15. Force-recreated backend services to load updated env.
16. Re-enabled profile schema gate (`COPILOT_REQUIRE_PROFILE_SCHEMA=true`).
17. Re-ran validator successfully.

## 5) Divergences From Default Docs (Debug + Audit)

These are the exact changes from a naive/default run path:

1. **Dependency path**: used `docker-compose-v2` (Ubuntu 24.04) instead of `docker-compose-plugin`.
2. **Execution context**: used `sg docker -c '...cmd...'` for Docker/Supabase commands when shell lacked docker group context.
3. **Supabase CLI install method**: used GitHub release tarball instead of `app.supabase.com` install URL.
4. **Supabase endpoint for containers**: used `http://172.17.0.1:54321` (container-reachable host gateway), not `127.0.0.1`.
5. **Disk-safe Supabase runtime**: operated with minimal active services to stay within 13G root volume.
6. **Authelia domain**: used `PUBLIC_DOMAIN=127.0.0.1` (not `localhost`).
7. **Schema gate posture**: final state restored to `COPILOT_REQUIRE_PROFILE_SCHEMA=true` after schema apply.

## 6) Failure Signature Index (Future Debugging)

Use this as quick triage when a future deploy fails differently.

### A) Docker socket permission denied

Signature:
- `permission denied while trying to connect to the Docker daemon socket`

Action:
- Run commands via `sg docker -c '...cmd...'`, or relogin after `usermod -aG docker "$USER"`.

### B) Supabase DB init fails with disk error

Signature:
- `initdb: ... No space left on device`

Action:
- Free disk before retry (`apt clean`, prune unused Docker artifacts/images), then restart Supabase.

### C) Supabase CLI installer script fails

Signature:
- `curl ... app.supabase.com/api/install/cli` returns 404/fails

Action:
- Install CLI from GitHub release tarball and ensure PATH includes `$HOME/.local/bin`.

### D) Backend cannot reach local Supabase

Signature:
- connection refused/timeouts when using `SUPABASE_URL=http://127.0.0.1:54321` from containers

Action:
- Set `SUPABASE_URL` to Docker host gateway URL (this run used `http://172.17.0.1:54321`), then force-recreate backend services.

### E) Authelia startup/domain error

Signature:
- cookie/domain validation failure with `localhost`

Action:
- Set `PUBLIC_DOMAIN=127.0.0.1` or valid FQDN; rerun `scripts/bootstrap_authelia.sh`.

### F) Runtime env not picked up after `.env` edits

Signature:
- behavior unchanged after env update

Action:
- `docker compose ... up -d --force-recreate ...` for affected services.

### G) Copilot profile schema gate failure

Signature:
- Copilot startup/profile schema validation failure

Action:
- Ensure `supabase_schema.sql` is applied to current DB and `SUPABASE_*` points at that DB.

## 7) Command Snippets Used for Validation (Handoff)

```bash
# Stack validator
sg docker -c 'cd /home/user1/study-agents/backend-vps && bash scripts/validate_zimaboard_stack.sh'

# Local Supabase status
sg docker -c 'cd /home/user1/study-agents/backend-vps && PATH=$HOME/.local/bin:$PATH supabase status -o env'

# Check effective env inside cag-service
sg docker -c 'docker exec backend-vps-cag-service-1 env | grep -E "^SUPABASE_(URL|DB_URL|KEY)="'
```

## 8) Artifacts Changed During This Run (Audit)

Primary docs edited:
- `ZIMABOARD_16GB_DEPLOYMENT.md`
- `README_BACKEND_VPS_QUICKSTART.md`
- `DEPLOYMENT_RUN_2026-03-26.md` (this file)

Additional artifact created by local Supabase init:
- `supabase/` directory under `backend-vps/`

## 9) What to Hand to Next Engineer/AI (Quick Context Block)

- This host deploy is **working** with local Supabase.
- Keep backend pointed at local gateway Supabase URL (`SUPABASE_URL=http://172.17.0.1:54321`) unless network topology changes.
- If anything breaks after edits, first rerun validator, then use Section 6 signature index.
- This log is the authoritative audit trail for the 2026-03-26 deployment run.

## 10) Post-Deploy LAN HTTPS Enablement (2026-03-27 UTC)

- Objective: browser access from LAN clients at `https://10.72.72.161/`.
- Initial issue: browser showed protocol/certificate errors during IP-based TLS access.
- Corrective changes applied:
  - set `.env`:
    - `PUBLIC_DOMAIN=10.72.72.161`
    - `AUTHELIA_OIDC_CLIENT_REDIRECT_URI=https://10.72.72.161/oidc/callback`
    - `GATEWAY_ALLOWED_CIDRS=127.0.0.1/32 ::1/128 10.72.72.0/24`
  - reran `scripts/bootstrap_authelia.sh`
  - force-recreated `authelia` and `tls-gateway`
  - updated `docker/Caddyfile` to support IP-LAN TLS behavior:
    - `tls internal` with RSA key type
    - `default_sni {$PUBLIC_DOMAIN}`
    - `servers { protocols h1 }`
- Verification:
  - `https://10.72.72.161/healthz` => `200`
  - `https://10.72.72.161/` => Authelia redirect/login flow

## 11) New helper automation added

- `scripts/configure_lan_https.sh`
  - applies LAN HTTPS domain/cidr settings and restarts gateway/auth
- `scripts/export_caddy_root_ca.sh`
  - exports Caddy local root CA cert and prints fingerprint/import hint
- `scripts/install_backend_vps.sh` actions added:
  - `configure-lan-https`
  - `export-caddy-ca`
  - Docker-dependent helper actions now auto-use `sg docker` fallback when direct Docker access is unavailable in current shell

## 12) Follow-up Runtime Event (2026-03-27 UTC)

- Trigger: `install_backend_vps.sh restart` after `.env` edit.
- Failure observed: image build failed with `OSError: [Errno 28] No space left on device` while `pip install` in `study-agents-python`.
- Immediate corrective action:
  - ran Docker/host cleanup (`builder prune`, `image prune`, `container prune`, `apt clean`, journal vacuum)
  - reclaimed ~4.4GB Docker space and restored free disk to ~6.6GB
- Retried restart and validation succeeded:
  - `cag-service` 200
  - `rag-service` 400
  - `copilot-service` 422
  - `copilot-frontend` 200
- Automation added:
  - `scripts/reclaim_disk_space.sh`
  - `scripts/install_backend_vps.sh reclaim-disk`

## 13) Follow-up Auth UX Note (2026-03-27 UTC)

- Observed behavior: during Authelia identity verification for 2FA enrollment, UI states one-time code was sent by email.
- Effective behavior on this deployment: notifier is filesystem-backed:
  - `notifier.filesystem.filename=/config/notification.txt`
  - no SMTP email delivery is configured by default.
- Operational retrieval command:
  - `docker compose ... exec -T authelia sh -lc 'grep -E "^[A-Z0-9]{8}$" /config/notification.txt | tail -n 1'`
- Documentation updated:
  - `README_BACKEND_VPS_QUICKSTART.md`
  - `ZIMABOARD_16GB_DEPLOYMENT.md`
