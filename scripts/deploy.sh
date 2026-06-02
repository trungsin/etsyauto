#!/usr/bin/env bash
# deploy.sh — pull latest code, sync deps, migrate DB, restart service
set -euo pipefail

REPO_DIR="/home/lesin/etsyauto"
BACKEND_DIR="$REPO_DIR/backend"

echo "[deploy] pulling latest code..."
cd "$REPO_DIR"
git pull origin master

echo "[deploy] syncing dependencies..."
cd "$BACKEND_DIR"
uv sync --quiet

echo "[deploy] running migrations..."
uv run alembic upgrade head

echo "[deploy] restarting service..."
sudo systemctl restart etsyauto

echo "[deploy] done — $(date -u '+%Y-%m-%d %H:%M UTC')"
