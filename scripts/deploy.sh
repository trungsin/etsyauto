#!/usr/bin/env bash
# deploy.sh — pull latest code, sync deps, migrate DB, restart service
set -euo pipefail

REPO_DIR="/home/lesin/etsyauto"
BACKEND_DIR="$REPO_DIR/backend"
UV="/home/lesin/.local/bin/uv"

echo "[deploy] pulling latest code..."
cd "$REPO_DIR"
git pull origin master

echo "[deploy] syncing dependencies..."
cd "$BACKEND_DIR"
$UV sync --quiet

echo "[deploy] running migrations..."
$UV run alembic upgrade head

echo "[deploy] restarting service..."
# User-level unit owns port 8787; XDG_RUNTIME_DIR needed for systemctl --user over non-interactive SSH
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
systemctl --user restart etsyauto-backend

echo "[deploy] done — $(date -u '+%Y-%m-%d %H:%M UTC')"
