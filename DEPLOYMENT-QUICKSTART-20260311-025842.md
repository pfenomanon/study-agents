# Deployment Quickstart (20260311-025842)

Artifacts generated:
- `study-agents-backend-vps-20260311-025842.tar.gz` (backend package for VPS)
- `study-agents-windows-client-20260311-025842.zip` (frontend/client package for Windows locals)

## Backend on a new VPS

1. Copy backend archive to VPS and extract:
```bash
scp study-agents-backend-vps-20260311-025842.tar.gz user@<vps-ip>:/opt/
ssh user@<vps-ip>
cd /opt
tar -xzf study-agents-backend-vps-20260311-025842.tar.gz
cd study-agents-backend-vps-20260311-025842
```
2. Install and configure:
```bash
bash scripts/install_backend_vps.sh deps
cp -n .env.example .env
bash scripts/generate_local_api_keys.sh --write-env
nano .env
```
3. Apply schema:
- Cloud: run `supabase_schema.sql` in Supabase SQL Editor.
- Optional CLI path: set `SUPABASE_DB_URL` in `.env`, then run:
```bash
bash scripts/install_backend_vps.sh apply-schema
```
4. Start:
```bash
bash scripts/install_backend_vps.sh start
```

## Client on a new Windows machine

1. Copy and extract `study-agents-windows-client-20260311-025842.zip`.
2. In Command Prompt:
```cmd
install_client.bat
```
3. Configure:
```cmd
notepad client_config.bat
```
4. Run:
```cmd
run_remote_image.bat
```
