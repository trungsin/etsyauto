# Code Standards

## Python (Backend)

### Version & Tooling
- Python 3.12+
- `uv` for dependency management (`pyproject.toml`)
- `alembic` for database migrations
- `pytest` for tests (no `unittest`)

### File Size
- Keep files under 200 lines. If a module grows beyond 200 lines, split by concern:
  - Extract DB query helpers into `services/`
  - Extract HTTP client calls into `clients/`
  - Extract prompt strings into `prompts/`

### Naming Conventions
- Files: `snake_case.py` with descriptive names (e.g., `etsy_api_client.py`, not `client.py`)
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private helpers: prefix with `_` (e.g., `_check_admin_token`)

### Type Annotations
- All function signatures must have full type annotations (parameters + return type)
- Use `X | None` over `Optional[X]` (Python 3.10+ union syntax)
- Use `list[str]` / `dict[str, int]` (lowercase generics, Python 3.9+)
- No bare `Any` — if unavoidable, add a comment explaining why

### Error Handling
- Always use `try/except` around external API calls
- Log exceptions with `logger.exception(...)` (includes stack trace)
- Never swallow exceptions silently
- Re-raise as `HTTPException` at route boundaries; let workers log and continue
- Use `db.rollback()` in all DB error paths before re-raising

### Database
- Use SQLAlchemy 2.0 `Mapped` + `mapped_column` style (not legacy `Column`)
- Always call `db.refresh(row)` after `db.commit()` if returning the row
- Use `session.query(Model).filter(...)` for simple queries
- Never use raw SQL strings — use ORM or `text()` with bound parameters

### Logging
```python
import logging
logger = logging.getLogger(__name__)
# Use structured log messages:
logger.info("worker: processing listing id=%s status=%s", listing.id, listing.status)
logger.exception("worker: unexpected error listing id=%s", listing.id)
```
- Log at INFO for normal state transitions
- Log at WARNING for degraded/non-fatal conditions
- Log at ERROR/EXCEPTION for failures requiring operator attention

### Configuration
- All config in `app/config.py` via `pydantic_settings.BaseSettings`
- Load from `backend/.env` — never hardcode secrets
- Default empty string `""` for optional credentials; workers check and skip gracefully

### Imports
- Standard library first, then third-party, then local (`app.*`)
- Use local imports inside functions to break circular dependencies (documented with comment)
- Never use wildcard imports (`from module import *`)

### Testing
- Test files in `backend/tests/`, named `test_<module>.py`
- Use `pytest` fixtures for DB sessions and mock clients
- Mock external HTTP calls (Etsy, Claude, Notion, R2, remove.bg, Gemini) — never call real APIs in tests
- One test file per major module; test both happy path and error/edge cases
- Run: `cd backend && uv run pytest -v`

### Worker Pattern
Workers follow this structure:
```python
def run_<worker>_job() -> None:
    """Top-level job function registered with APScheduler."""
    with Session(engine) as session:
        rows = _fetch_eligible_listings(session)
        for row in rows:
            try:
                _process_one(session, row)
            except Exception:
                logger.exception("worker: failed listing id=%s", row.id)
                row.status = "failed"
                session.commit()

def _fetch_eligible_listings(session: Session) -> list[Listing]:
    return session.query(Listing).filter(Listing.status == "target-status").all()

def _process_one(session: Session, listing: Listing) -> None:
    listing.status = "processing-status"
    session.commit()
    # ... do work ...
    listing.status = "next-status"
    session.commit()
```

---

## JavaScript (Chrome Extension)

### Version & Tooling
- Vanilla JS (ES2022+) — no build step, no bundler (MV3 service worker constraint)
- No external npm dependencies in extension
- `chrome.*` APIs only — no Node.js APIs

### Naming
- Files: `kebab-case.js` (e.g., `service-worker.js`, `listing-detector.js`)
- Functions: `camelCase`
- Constants: `UPPER_SNAKE_CASE`
- Classes: `PascalCase` (avoid where possible — prefer plain functions)

### Error Handling
```javascript
// Always handle chrome.runtime.lastError in callbacks
chrome.storage.local.get(['token'], (result) => {
  if (chrome.runtime.lastError) {
    console.error('[etsyauto] storage error:', chrome.runtime.lastError.message);
    return;
  }
  // use result
});

// Use try/catch for fetch calls
try {
  const resp = await fetch('http://localhost:8787/ingest', { method: 'POST', ... });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
} catch (err) {
  console.error('[etsyauto] ingest failed:', err);
}
```

### MV3 Constraints
- Service worker has no DOM access — no `window`, `document`
- Use `chrome.storage.local` (not `localStorage`)
- Long-lived connections via `chrome.runtime.connect` (not `chrome.runtime.sendMessage` for streaming)
- All fetch calls from service worker (not content script) for cross-origin requests

### Content Script Rules
- Minimize DOM mutation — read listing data, do not modify Etsy UI
- Communicate via `chrome.runtime.sendMessage` to service worker
- Guard all selectors: `document.querySelector(selector)?.textContent ?? ''`

---

## Documentation

### Code Comments
- Add docstring to every module (top of file) explaining purpose
- Add docstring to every class and public function
- Inline comments for non-obvious logic only — prefer self-documenting names

### Docstring Format (Python)
```python
def build_auth_url(state: str, code_challenge: str) -> str:
    """Construct the Etsy OAuth2 authorization URL with PKCE challenge.

    Args:
        state: CSRF state token (random URL-safe string)
        code_challenge: S256-hashed PKCE code verifier

    Returns:
        Full authorization URL to redirect the user to.
    """
```

---

## Git

### Commit Messages
Use conventional commits:
- `feat:` new feature
- `fix:` bug fix
- `refactor:` code restructure without behavior change
- `test:` test additions/changes
- `docs:` documentation only

No AI references. No "WIP" commits to main.

### What NOT to Commit
- `backend/.env` (API keys)
- `backend/etsyauto.db` (SQLite file — user data)
- `__pycache__/`, `.pytest_cache/`, `*.pyc`
- `backend/.venv/`

---

## Security Standards

- No secrets in source code (use `settings.*` from `config.py`)
- Validate all external input at route boundaries (Pydantic models with constraints)
- No `eval()` or `exec()` in Python; no `innerHTML` with user data in JS
- All DB queries use ORM (no raw string interpolation)
- X-Admin-Token required for all `/admin/*` endpoints
- CORS restricted to known origins (extension + localhost only)
