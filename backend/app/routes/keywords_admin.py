"""Keywords admin HTML router — list/add/toggle/delete keywords and trigger manual mining.

All routes are protected by the same X-Admin-Token / cookie / ?token= auth used
by templates_admin.py. The HTMX partial for row toggle returns only a <tr> so the
DOM updates inline without a full page reload.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Cookie, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.services import idea_service, idea_miner_service, keyword_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/keywords", tags=["admin-keywords"])


# ---------------------------------------------------------------------------
# Auth helper (mirrors templates_admin._check_token)
# ---------------------------------------------------------------------------


def _check_token(
    x_admin_token: str | None,
    request: Request | None = None,
    admin_token_cookie: str | None = None,
) -> None:
    """Raise 401 if token missing/wrong.

    Priority: X-Admin-Token header → admin_token cookie → ?token= query param.
    """
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    candidates = [x_admin_token, admin_token_cookie]
    if request is not None:
        candidates.append(request.query_params.get("token"))
    if not any(c == settings.admin_token for c in candidates if c):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


def _get_jinja():
    """Lazy import of shared Jinja2Templates to avoid circular imports at module load."""
    from app.main import jinja_templates
    return jinja_templates


# ---------------------------------------------------------------------------
# GET /admin/keywords — keyword list + add form
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def list_keywords_ui(
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Render keyword list with add-new form."""
    _check_token(x_admin_token, request, admin_token)
    keywords = keyword_service.list_keywords(db)
    rows = [
        {
            "id": kw.id,
            "term": kw.term,
            "enabled": kw.enabled,
            "last_run_at": kw.last_run_at,
            "idea_count": idea_service.count_by_keyword(db, kw.id),
        }
        for kw in keywords
    ]
    return _get_jinja().TemplateResponse(
        request,
        "keywords/list.html",
        {"keywords": rows, "error": None, "flash": None},
    )


# ---------------------------------------------------------------------------
# POST /admin/keywords — create new keyword
# ---------------------------------------------------------------------------


@router.post("", response_model=None)
def create_keyword_ui(
    request: Request,
    term: str = Form(...),
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> Response:
    """Handle add-keyword form submission. Redirects on success; re-renders on duplicate."""
    _check_token(x_admin_token, request, admin_token)
    term = term.strip()
    if not term:
        keywords = keyword_service.list_keywords(db)
        rows = [
            {
                "id": kw.id,
                "term": kw.term,
                "enabled": kw.enabled,
                "last_run_at": kw.last_run_at,
                "idea_count": idea_service.count_by_keyword(db, kw.id),
            }
            for kw in keywords
        ]
        return _get_jinja().TemplateResponse(
            request,
            "keywords/list.html",
            {"keywords": rows, "error": "Term cannot be empty.", "flash": None},
            status_code=400,
        )

    try:
        keyword_service.create_keyword(db, term)
    except ValueError:
        keywords = keyword_service.list_keywords(db)
        rows = [
            {
                "id": kw.id,
                "term": kw.term,
                "enabled": kw.enabled,
                "last_run_at": kw.last_run_at,
                "idea_count": idea_service.count_by_keyword(db, kw.id),
            }
            for kw in keywords
        ]
        return _get_jinja().TemplateResponse(
            request,
            "keywords/list.html",
            {"keywords": rows, "error": f"Keyword '{term}' already exists.", "flash": None},
            status_code=400,
        )

    return RedirectResponse(url="/admin/keywords", status_code=303)


# ---------------------------------------------------------------------------
# POST /admin/keywords/{id}/toggle — enable/disable (HTMX partial)
# ---------------------------------------------------------------------------


@router.post("/{kid}/toggle", response_class=HTMLResponse)
def toggle_keyword_ui(
    kid: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Flip enabled state and return the updated <tr> partial for HTMX swap."""
    _check_token(x_admin_token, request, admin_token)
    kw_obj = keyword_service.get_keyword(db, kid)
    if kw_obj is None:
        raise HTTPException(status_code=404, detail="Keyword not found")

    updated = keyword_service.toggle_keyword(db, kid, not kw_obj.enabled)
    row = {
        "id": updated.id,
        "term": updated.term,
        "enabled": updated.enabled,
        "last_run_at": updated.last_run_at,
        "idea_count": idea_service.count_by_keyword(db, updated.id),
    }
    return _get_jinja().TemplateResponse(
        request,
        "keywords/_row.html",
        {"kw": row},
    )


# ---------------------------------------------------------------------------
# POST /admin/keywords/{id}/run — trigger manual mining (background thread)
# ---------------------------------------------------------------------------


@router.post("/{kid}/run", response_model=None)
def run_keyword_ui(
    kid: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> Response:
    """Dispatch a background mining run for one keyword; redirect immediately with flash."""
    _check_token(x_admin_token, request, admin_token)
    kw_obj = keyword_service.get_keyword(db, kid)
    if kw_obj is None:
        raise HTTPException(status_code=404, detail="Keyword not found")

    def _mine(keyword_id: int) -> None:
        """Thread target — opens its own DB session; errors logged, never raised."""
        session = SessionLocal()
        try:
            idea_miner_service.run_for_keyword(session, keyword_id)
        except Exception:
            logger.exception("Background mining failed for keyword id=%d", keyword_id)
        finally:
            session.close()

    thread = threading.Thread(target=_mine, args=(kid,), daemon=True)
    thread.start()

    # Redirect back to list with flash via query param
    return RedirectResponse(
        url="/admin/keywords?flash=Mining+started+in+background",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# POST /admin/keywords/{id}/delete — delete keyword
# ---------------------------------------------------------------------------


@router.post("/{kid}/delete", response_model=None)
def delete_keyword_ui(
    kid: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> Response:
    """HTML form POST delete (browsers can't send DELETE natively)."""
    _check_token(x_admin_token, request, admin_token)
    try:
        keyword_service.delete_keyword(db, kid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/keywords", status_code=303)
