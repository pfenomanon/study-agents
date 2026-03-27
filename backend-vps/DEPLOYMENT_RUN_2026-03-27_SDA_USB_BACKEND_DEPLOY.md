# Backend-VPS USB `sda` Deployment Run Log

## Metadata
- Date (UTC): 2026-03-27
- Host: study-agents-backend
- Requested storage target: `/dev/sda` (USB)
- Repo location (source): `/home/user1/study-agents/backend-vps` on internal eMMC
- Objective: deploy backend-vps with runtime artifacts on USB `sda` while keeping repo on eMMC

## Chronological Execution Log
- [2026-03-27 02:14:10 UTC] Baseline confirmed: root FS on eMMC (`/dev/mapper/ubuntu--vg-ubuntu--lv`), USB root partition `/dev/sda2` present but not mounted.
- [2026-03-27 02:14:10 UTC] Baseline confirmed: backend-vps and local Supabase stacks healthy before USB storage cutover.
- [2026-03-27 02:14:10 UTC] Baseline confirmed: root filesystem pressure high (94% used, ~762MB free), requiring runtime migration to USB.
- [2026-03-27 02:14:10 UTC] Failure: initial log bootstrap used an unquoted heredoc with markdown backticks; shell interpreted embedded command substitutions and stripped literal values.
- [2026-03-27 02:14:10 UTC] Corrective action: rebuilt log using a quoted heredoc and placeholder substitution to preserve literal markdown safely.
- [2026-03-27 02:14:19 UTC] Success: mounted /dev/sda2 at /mnt/usbroot.
- [2026-03-27 02:14:19 UTC] Success: persisted USB mount in /etc/fstab with UUID=7759fec5-0f06-4f05-a1b9-f4679e89203b.
- [2026-03-27 02:14:37 UTC] Baseline: Docker root before migration = /var/lib/docker.
- [2026-03-27 02:14:48 UTC] Success: stopped Docker service for consistent data migration.
- [2026-03-27 02:14:48 UTC] Success: created /etc/docker/daemon.json with USB data-root (/mnt/usbroot/docker).
- [2026-03-27 02:17:39 UTC] Success: rsynced Docker runtime data from /var/lib/docker to /mnt/usbroot/docker.
- [2026-03-27 02:18:17 UTC] Success: Docker restarted with data-root on USB (/mnt/usbroot/docker).
- [2026-03-27 02:18:30 UTC] Audit: post-cutover size snapshot old=/var/lib/docker:7.2G, usb=/mnt/usbroot/docker:20G.
- [2026-03-27 02:18:34 UTC] Success: removed stale legacy Docker directory on eMMC to reclaim root disk space after USB cutover.
- [2026-03-27 02:19:18 UTC] Success: ran documented one-command path 'bash scripts/install_backend_vps.sh start-local-all' from backend-vps/.
- [2026-03-27 02:19:18 UTC] Observation: installer detected existing Docker install and existing running Supabase; continued idempotently without manual intervention.
- [2026-03-27 02:19:18 UTC] Observation: Docker Compose emitted non-blocking warning about missing buildx/Bake; cached builds succeeded and deployment continued.
- [2026-03-27 02:19:18 UTC] Success: schema apply completed (existing objects skipped with NOTICE) and backend stack validation passed (CAG 200, RAG 400, Copilot 422, Frontend 200).
- [2026-03-27 02:19:18 UTC] Audit: post-deploy DockerRootDir=/mnt/usbroot/docker.
- [2026-03-27 02:19:18 UTC] Audit: disk snapshot root='/dev/mapper/ubuntu--vg-ubuntu--lv   13G  4.2G  8.0G  35% /'.
- [2026-03-27 02:19:18 UTC] Audit: disk snapshot usb='/dev/sda2       468G   20G  425G   5% /mnt/usbroot'.
- [2026-03-27 02:19:48 UTC] Failure: 'configure-lan-https' failed because docker/authelia directory was root-owned (700), causing bootstrap key write permission denied.
- [2026-03-27 02:19:48 UTC] Corrective action: reset ownership to user1:user1 and relaxed directory mode to 755 for docker/authelia to allow bootstrap_authelia.sh writes.
- [2026-03-27 02:20:16 UTC] Success: reran LAN HTTPS configuration for 10.72.72.161 with allow CIDR 10.72.72.0/24 after permission fix.
- [2026-03-27 02:20:33 UTC] Failure: export-caddy-ca script wrote root-owned cert via docker bind mount and then could not read it as user1 (openssl permission denied).
- [2026-03-27 02:20:34 UTC] Corrective action: reran export script with sudo, then reassigned cert ownership to user1 and normalized mode to 644 for non-root readability.
- [2026-03-27 02:20:34 UTC] Success: exported and verified Caddy local root CA certificate at /home/user1/caddy-local-root.crt.
- [2026-03-27 02:20:34 UTC] Validation: https://10.72.72.161/healthz returned HTTP 200.
- [2026-03-27 02:20:34 UTC] Validation: gateway root response first header='HTTP/1.1 302 Found'.
- [2026-03-27 02:20:51 UTC] Success: ran scripts/validate_zimaboard_stack.sh after LAN HTTPS and CA export corrections.
- [2026-03-27 02:20:51 UTC] Validation result: CAG 200, RAG 400, Copilot 422, Frontend 200 (all expected/acceptable).
- [2026-03-27 02:20:51 UTC] Audit: docker_root=/mnt/usbroot/docker, root_fs=/dev/mapper/ubuntu--vg-ubuntu--lv, usb_mount=/dev/sda2.
- [2026-03-27 02:20:51 UTC] Audit: final disk snapshot root='/dev/mapper/ubuntu--vg-ubuntu--lv   13G  4.2G  8.0G  35% /'.
- [2026-03-27 02:20:51 UTC] Audit: final disk snapshot usb='/dev/sda2       468G   20G  425G   5% /mnt/usbroot'.
- [2026-03-27 02:32:17 UTC] Requirement clarification applied: migrated active repo runtime from eMMC path to USB path '/mnt/usbroot/study-agents/backend-vps' so all backend bind mounts resolve from USB.
- [2026-03-27 02:32:17 UTC] Failure during USB cutover validation: cag-service returned HTTP 500 due write permission denied on '/app/data/qa_sessions/qa_log.md' after rsync.
- [2026-03-27 02:32:17 UTC] Corrective action: set writable mode for qa log on USB repo data path, then re-ran validator successfully.
- [2026-03-27 02:32:17 UTC] Final verification: backend/supabase runtime mounts resolve to /mnt/usbroot/... and stack validation passed (CAG 200, RAG 400, Copilot 422, Frontend 200).
