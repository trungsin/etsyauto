"""Ideas admin HTML router — browse mined ideas with filter/sort, detail view, reject action.

All routes are protected by the same X-Admin-Token / cookie / ?token= auth used
across the admin UI. Ideas are sourced from idea_service.list_ideas with optional
filters (keyword_id, status, source) and sort (velocity, recency, favorers).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import idea_service, keyword_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ideas", tags=["admin-ideas"])

# Valid status values for filter dropdown — matches Idea.status domain
_VALID_STATUSES = ("new", "reviewed", "rejected", "drafted")
# Valid source values surfaced in the miner to date
_VALID_SOURCES = ("etsy_api", "extension_passive", "printful")
# Valid sort keys
_VALID_SORTS = ("recency", "velocity", "favorers")


# ---------------------------------------------------------------------------
# Auth helper (mirrors templates_admin._check_token)
# ---------------------------------------------------------------------------


def _check_token(
    x_admin_token: str | None,
    request: Request | None = None,
    admin_token_cookie: str | None = None,
) -> None:
    """Raise 401 if token missing/wrong."""
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
# GET /admin/ideas — filterable, sortable list with pagination
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def list_ideas_ui(
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
    keyword_id: int | None = None,
    status: str | None = None,
    source: str | None = None,
    sort: str = "recency",
    offset: int = 0,
) -> HTMLResponse:
    """Render the ideas list with filter form and paginated results table."""
    _check_token(x_admin_token, request, admin_token)

    # Sanitise enum-like params to prevent injection into SQL via sort key
    if sort not in _VALID_SORTS:
        sort = "recency"
    if status and status not in _VALID_STATUSES:
        status = None
    if source and source not in _VALID_SOURCES:
        source = None

    ideas = idea_service.list_ideas(
        db,
        keyword_id=keyword_id,
        status=status,
        source=source,
        sort=sort,
        limit=50,
        offset=offset,
    )

    # Enrich rows with velocity (for display) and latest favorer count
    rows = []
    for idea in ideas:
        latest_sig = idea_service.latest_signal(db, idea.id)
        rows.append(
            {
                "id": idea.id,
                "title": idea.title or "(no title)",
                "source": idea.source,
                "status": idea.status,
                "keyword_id": idea.keyword_id,
                "num_favorers": latest_sig.num_favorers if latest_sig else None,
                "velocity": round(idea_service.compute_velocity(db, idea.id), 2),
                "created_at": idea.created_at,
                "reference_image_url": idea.reference_image_url,
            }
        )

    keywords = keyword_service.list_keywords(db)

    return _get_jinja().TemplateResponse(
        request,
        "ideas/list.html",
        {
            "ideas": rows,
            "keywords": keywords,
            "filter_keyword_id": keyword_id,
            "filter_status": status or "",
            "filter_source": source or "",
            "filter_sort": sort,
            "offset": offset,
            "limit": 50,
            "valid_statuses": _VALID_STATUSES,
            "valid_sources": _VALID_SOURCES,
            "valid_sorts": _VALID_SORTS,
        },
    )


# ---------------------------------------------------------------------------
# GET /admin/ideas/{id} — idea detail page
# ---------------------------------------------------------------------------


@router.get("/{iid}", response_class=HTMLResponse)
def idea_detail_ui(
    iid: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Render full idea detail: metadata, image, signal history, action buttons."""
    _check_token(x_admin_token, request, admin_token)

    idea = idea_service.get_idea(db, iid)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    signals = idea_service.list_signals(db, iid, limit=20)

    # Parse JSON fields for template rendering — degrade gracefully on bad data
    def _parse_json_list(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    tags = _parse_json_list(idea.tags_json)
    materials = _parse_json_list(idea.materials_json)

    return _get_jinja().TemplateResponse(
        request,
        "ideas/detail.html",
        {
            "idea": idea,
            "tags": tags,
            "materials": materials,
            "signals": signals,
            "velocity": round(idea_service.compute_velocity(db, iid), 2),
        },
    )


# ---------------------------------------------------------------------------
# POST /admin/ideas/{id}/reject — set status=rejected
# ---------------------------------------------------------------------------


@router.post("/{iid}/reject", response_model=None)
def reject_idea_ui(
    iid: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> Response:
    """Mark idea as rejected and redirect back to ideas list."""
    _check_token(x_admin_token, request, admin_token)
    try:
        idea_service.reject_idea(db, iid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/ideas", status_code=303)
