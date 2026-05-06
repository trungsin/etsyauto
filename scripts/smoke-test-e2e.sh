#!/usr/bin/env bash
# smoke-test-e2e.sh — structural readiness check for EtsyAuto
# Tests: deps installed, migrations applied, pytest passes, /health responds,
#        extension manifest valid.
# Does NOT require real API keys — validates system integration, not external services.
# Usage:  bash scripts/smoke-test-e2e.sh
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/../backend" && pwd)"
EXTENSION_DIR="$(cd "$(dirname "$0")/../extension" && pwd)"
PORT=8788   # use alternate port to avoid conflict with dev server
PASS=0
FAIL=0
SERVER_PID=""

red="\033[0;31m"
green="\033[0;32m"
yellow="\033[0;33m"
reset="\033[0m"

ok()   { echo -e "${green}[PASS]${reset} $*"; PASS=$((PASS + 1)); }
fail() { echo -e "${red}[FAIL]${reset} $*"; FAIL=$((FAIL + 1)); }
info() { echo -e "${yellow}[INFO]${reset} $*"; }

cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    info "Killed uvicorn (pid=$SERVER_PID)"
  fi
}
trap cleanup EXIT

echo ""
echo "========================================"
echo "  EtsyAuto Smoke Test — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "========================================"
echo ""

# ── 1. uv sync ───────────────────────────────────────────────────────────────
info "Installing Python dependencies (uv sync)..."
cd "$BACKEND_DIR"
if uv sync --quiet 2>&1; then
  ok "uv sync completed"
else
  fail "uv sync failed"
fi

# ── 2. Alembic migrations ─────────────────────────────────────────────────────
info "Running Alembic migrations..."
migration_out=$(uv run alembic upgrade head 2>&1)
migration_rc=$?
if [[ $migration_rc -ne 0 ]] || echo "$migration_out" | grep -qiE "^(ERROR|CRITICAL|Traceback)"; then
  fail "Alembic migration failed (rc=$migration_rc): $migration_out"
else
  ok "Alembic migrations applied"
fi

# ── 3. pytest ─────────────────────────────────────────────────────────────────
info "Running pytest..."
if uv run pytest -v --tb=short 2>&1; then
  ok "All pytest tests passed"
else
  fail "pytest reported failures"
fi

# ── 4. Start uvicorn in background ───────────────────────────────────────────
info "Starting uvicorn on port $PORT..."
ANTHROPIC_API_KEY="" \
ETSY_API_KEY="" \
ETSY_SHARED_SECRET="" \
REMOVEBG_API_KEY="" \
GEMINI_API_KEY="" \
NOTION_API_KEY="" \
NOTION_DATABASE_ID="" \
R2_ACCOUNT_ID="" \
R2_ACCESS_KEY_ID="" \
R2_SECRET_ACCESS_KEY="" \
R2_BUCKET_NAME="" \
R2_PUBLIC_URL="" \
ADMIN_TOKEN="" \
DATABASE_URL="sqlite:///./etsyauto_smoke.db" \
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
SERVER_PID=$!

info "Waiting 4s for server to start (pid=$SERVER_PID)..."
sleep 4

# ── 5. Health check ───────────────────────────────────────────────────────────
info "Curling /health..."
HTTP_STATUS=$(curl -s -o /tmp/health_response.json -w "%{http_code}" "http://127.0.0.1:$PORT/health" 2>/dev/null || echo "000")
HEALTH_BODY=$(cat /tmp/health_response.json 2>/dev/null || echo "{}")

if [[ "$HTTP_STATUS" == "200" ]]; then
  ok "/health returned 200 — body: $HEALTH_BODY"
elif [[ "$HTTP_STATUS" == "503" ]]; then
  # 503 is acceptable in smoke test — scheduler depends on jobs registered
  ok "/health reachable (503 degraded is expected without real config) — body: $HEALTH_BODY"
else
  fail "/health returned HTTP $HTTP_STATUS (expected 200 or 503)"
fi

# Stop server
kill "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
sleep 1

# Clean up smoke test db
rm -f "$BACKEND_DIR/etsyauto_smoke.db"

# ── 6. Extension manifest validation ─────────────────────────────────────────
info "Validating extension manifest.json..."
if [[ -f "$EXTENSION_DIR/manifest.json" ]]; then
  MV=$(python3 -c "import json,sys; d=json.load(open('$EXTENSION_DIR/manifest.json')); print(d.get('manifest_version',0))" 2>/dev/null || echo "0")
  if [[ "$MV" == "3" ]]; then
    ok "manifest.json present, manifest_version=3"
  else
    fail "manifest.json found but manifest_version=$MV (expected 3)"
  fi
else
  fail "manifest.json not found at $EXTENSION_DIR/manifest.json"
fi

# ── 7. Check key extension files exist ───────────────────────────────────────
info "Checking extension files..."
EXT_FILES=(
  "background/service-worker.js"
  "side-panel/side-panel.html"
  "side-panel/side-panel.js"
  "content-scripts/listing-detector.js"
)
all_ext_ok=true
for f in "${EXT_FILES[@]}"; do
  if [[ -f "$EXTENSION_DIR/$f" ]]; then
    ok "extension/$f exists"
  else
    fail "extension/$f missing"
    all_ext_ok=false
  fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
TOTAL=$((PASS + FAIL))
if [[ "$FAIL" -eq 0 ]]; then
  echo -e "${green}RESULT: PASS${reset}  ($PASS/$TOTAL checks passed)"
else
  echo -e "${red}RESULT: FAIL${reset}  ($PASS/$TOTAL passed, $FAIL failed)"
fi
echo "========================================"
echo ""

[[ "$FAIL" -eq 0 ]]
