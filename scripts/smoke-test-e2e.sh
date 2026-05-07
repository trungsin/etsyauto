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

# Stop server (from step 4)
kill "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
sleep 1

# Clean up smoke test db
rm -f "$BACKEND_DIR/etsyauto_smoke.db"

# ── 6. Admin template API smoke check ────────────────────────────────────────
# Start a fresh server with a migrated DB to verify /admin/templates auth guard.
info "Running migrations for admin template smoke DB..."
SMOKE_ADMIN_TOKEN="smoke-admin-token-$$"
SMOKE_DB="$BACKEND_DIR/etsyauto_smoke_admin.db"
rm -f "$SMOKE_DB"

# Run alembic against the admin smoke DB
DATABASE_URL="sqlite:///$SMOKE_DB" \
uv run alembic upgrade head 2>&1 | grep -v "^$" || true

info "Starting server for /admin/templates check (pid=...)..."
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
ADMIN_TOKEN="$SMOKE_ADMIN_TOKEN" \
DATABASE_URL="sqlite:///$SMOKE_DB" \
uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
SERVER_PID=$!
info "Waiting 4s for server (pid=$SERVER_PID)..."
sleep 4

# Check auth guard — no token → 401
UNAUTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://127.0.0.1:$PORT/admin/templates" 2>/dev/null || echo "000")

if [[ "$UNAUTH_STATUS" == "401" ]]; then
  ok "/admin/templates GET 401 without token (auth guard working)"
else
  fail "/admin/templates without token returned HTTP $UNAUTH_STATUS (expected 401)"
fi

# Check valid token → 200
ADMIN_STATUS=$(curl -s -o /tmp/admin_templates.json -w "%{http_code}" \
  -H "X-Admin-Token: $SMOKE_ADMIN_TOKEN" \
  "http://127.0.0.1:$PORT/admin/templates" 2>/dev/null || echo "000")

if [[ "$ADMIN_STATUS" == "200" ]]; then
  ok "/admin/templates GET 200 with valid admin token"
else
  fail "/admin/templates returned HTTP $ADMIN_STATUS (expected 200)"
fi

# ── 6b. Listing Creator endpoints (sub-feature C, v0.4.0) ────────────────────
info "Checking Creator-mode endpoints exist (auth-protected)..."

CREATOR_TEMPLATES_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-Admin-Token: $SMOKE_ADMIN_TOKEN" \
  "http://127.0.0.1:$PORT/templates" 2>/dev/null || echo "000")
if [[ "$CREATOR_TEMPLATES_STATUS" == "200" ]]; then
  ok "/templates GET 200 with admin token (Creator-mode source)"
else
  fail "/templates returned HTTP $CREATOR_TEMPLATES_STATUS (expected 200)"
fi

CREATOR_DESIGNS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-Admin-Token: $SMOKE_ADMIN_TOKEN" \
  "http://127.0.0.1:$PORT/designs?source_type=upload" 2>/dev/null || echo "000")
if [[ "$CREATOR_DESIGNS_STATUS" == "200" ]]; then
  ok "/designs?source_type=upload GET 200"
else
  fail "/designs returned HTTP $CREATOR_DESIGNS_STATUS (expected 200)"
fi

# from-template auth guard — no token → 401
CREATOR_NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST -H "Content-Type: application/json" \
  -d '{"template_id":1,"design_id":1,"title":"x","description":"y","shop_id":"1","enabled_combos":[]}' \
  "http://127.0.0.1:$PORT/listings/from-template" 2>/dev/null || echo "000")
if [[ "$CREATOR_NOAUTH" == "401" ]]; then
  ok "/listings/from-template POST 401 without token (auth guard working)"
else
  fail "/listings/from-template without token returned HTTP $CREATOR_NOAUTH (expected 401)"
fi

# from-template with token but missing data → 4xx (not 5xx)
CREATOR_AUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST -H "Content-Type: application/json" \
  -H "X-Admin-Token: $SMOKE_ADMIN_TOKEN" \
  -d '{"template_id":99,"design_id":99,"title":"x","description":"y","shop_id":"1","enabled_combos":[{"size":"S","color":"White"}]}' \
  "http://127.0.0.1:$PORT/listings/from-template" 2>/dev/null || echo "000")
if [[ "$CREATOR_AUTH" =~ ^4[0-9][0-9]$ ]]; then
  ok "/listings/from-template POST $CREATOR_AUTH with auth + missing template (expected 4xx)"
else
  fail "/listings/from-template returned HTTP $CREATOR_AUTH (expected 4xx, e.g. 404)"
fi

# Listing Creator admin UI page
ADMIN_UI_NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://127.0.0.1:$PORT/admin/listings/creator" 2>/dev/null || echo "000")
if [[ "$ADMIN_UI_NOAUTH" == "401" ]]; then
  ok "/admin/listings/creator GET 401 without token"
else
  fail "/admin/listings/creator no-token returned HTTP $ADMIN_UI_NOAUTH (expected 401)"
fi

ADMIN_UI_AUTH=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-Admin-Token: $SMOKE_ADMIN_TOKEN" \
  "http://127.0.0.1:$PORT/admin/listings/creator" 2>/dev/null || echo "000")
if [[ "$ADMIN_UI_AUTH" == "200" ]]; then
  ok "/admin/listings/creator GET 200 with token"
else
  fail "/admin/listings/creator with token returned HTTP $ADMIN_UI_AUTH (expected 200)"
fi

kill "$SERVER_PID" 2>/dev/null || true
SERVER_PID=""
sleep 1
rm -f "$SMOKE_DB"

# ── 6c. cv2 import sanity (v0.6.0 dependency) ────────────────────────────────
info "cv2 import check..."
if uv run python -c "import cv2; print(cv2.__version__)" >/dev/null 2>&1; then
  ok "cv2 imports OK (opencv-python-headless)"
else
  fail "cv2 import failed (opencv-python-headless not installed?)"
fi

# ── 7. Extension manifest validation ─────────────────────────────────────────
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

# ── 8. Check key extension files exist ───────────────────────────────────────
info "Checking extension files..."
EXT_FILES=(
  "background/service-worker.js"
  "side-panel/side-panel.html"
  "side-panel/side-panel.js"
  "side-panel/creator-mode.js"
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
