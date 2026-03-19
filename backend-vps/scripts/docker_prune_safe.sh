#!/usr/bin/env bash
set -euo pipefail

echo "Docker disk usage (before):"
docker system df
echo

echo "Pruning build cache..."
docker builder prune -af
echo

echo "Pruning unused images..."
docker image prune -af
echo

echo "Docker disk usage (after):"
docker system df
echo

cat <<'EOF'
Completed safe prune.
Volumes were not pruned. Persistent DB/storage data remains intact.
EOF
