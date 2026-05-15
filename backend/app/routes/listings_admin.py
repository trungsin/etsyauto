"""Admin CRUD routes for listings (v0.10).

Mounted at `/admin/listings`. Coexists with `/admin/listings/creator` (legacy
template-driven listing creator UI in templates_admin.py): paths don't overlap
(this router uses bare `""` and `/{id}/...` while creator uses `/creator/...`).

Auth: X-Admin-Token header OR admin_token cookie (same pattern as templates_admin).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Cookie, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.etsy_api_client import EtsyApiClient
from app.config import settings
from app.database import get_db
from app.models.listing import Listing
from app.models.template import Template
from app.services import (
    composite_service,
    etsy_taxonomy,
    listing_creator_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/listings", tags=["admin-listings"])


# ---------------------------------------------------------------------------
# Auth + Jinja helpers (mirrors templates_admin.py pattern)
# ---------------------------------------------------------------------------


def _check_token(
    x_admin_token: str | None,
    request: Request | None = None,
    admin_token_cookie: str | None = None,
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    candidates = [x_admin_token, admin_token_cookie]
    if request is not None:
        candidates.append(request.query_params.get("token"))
        candidates.append(request.query_params.get("admin_token"))
    if not any(c == settings.admin_token for c in candidates if c):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


_jinja: Jinja2Templates | None = None


def get_jinja() -> Jinja2Templates:
    global _jinja  # noqa: PLW0603
    if _jinja is None:
        from app.main import jinja_templates
        _jinja = jinja_templates
    return _jinja


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_shop_id() -> int:
    """Resolve Etsy shop_id from settings; raise 503 if unset."""
    shop_id = settings.etsy_shop_id if hasattr(settings, "etsy_shop_id") else None
    if not shop_id:
        # Fallback: try to read from any ApiCredential row with shop_id populated
        from app.models.api_credential import ApiCredential
        from app.database import SessionLocal
        with SessionLocal() as s:
            cred = s.scalars(
                select(ApiCredential).where(ApiCredential.shop_id.isnot(None))
            ).first()
            if cred and cred.shop_id:
                return int(cred.shop_id)
        raise HTTPException(status_code=503, detail="Etsy shop_id not configured")
    return int(shop_id)


def _row_to_view(row: Listing, template: Template | None) -> dict:
    """Shape Listing row for templates — parses local_payload."""
    try:
        payload = json.loads(row.local_payload_json or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "id": row.id,
        "etsy_listing_id": row.etsy_listing_id,
        "status": row.status,
        "title": payload.get("title") or row.original_title,
        "description": payload.get("description") or row.original_desc,
        "tags": payload.get("tags") or json.loads(row.original_tags or "[]"),
        "enabled_combos": payload.get("enabled_combos") or [],
        "zone_designs": payload.get("zone_designs") or {},
        "gallery": payload.get("gallery_snapshot") or [],
        "template_id": row.template_id,
        "template_name": template.name if template else None,
        "design_id": row.design_id,
        "pushed_at": row.pushed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "last_push_error": row.last_push_error,
        "deleted_at": row.deleted_at,
    }


# ---------------------------------------------------------------------------
# GET /admin/listings — list page
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def list_listings_ui(
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
    status: str | None = None,
    template_id: int | None = None,
    offset: int = 0,
) -> HTMLResponse:
    """Render the listings list with filter + pagination."""
    _check_token(x_admin_token, request, admin_token)

    LIMIT = 50
    stmt = select(Listing).where(Listing.deleted_at.is_(None))
    if status:
        stmt = stmt.where(Listing.status == status)
    if template_id:
        stmt = stmt.where(Listing.template_id == template_id)
    stmt = stmt.order_by(Listing.created_at.desc()).offset(offset).limit(LIMIT)
    rows = list(db.scalars(stmt).all())

    # Preload templates in one query
    tids = {r.template_id for r in rows if r.template_id}
    tmpl_by_id: dict[int, Template] = {}
    if tids:
        for t in db.scalars(select(Template).where(Template.id.in_(tids))).all():
            tmpl_by_id[t.id] = t

    listings = [_row_to_view(r, tmpl_by_id.get(r.template_id)) for r in rows]
    all_templates = list(db.scalars(select(Template).order_by(Template.name)).all())

    return get_jinja().TemplateResponse(
        request,
        "listings/list.html",
        {
            "listings": listings,
            "templates": all_templates,
            "filter_status": status or "",
            "filter_template_id": template_id,
            "offset": offset,
            "limit": LIMIT,
            "valid_statuses": ["new", "uploading", "created", "syncing", "failed", "deleted"],
        },
    )


# ---------------------------------------------------------------------------
# GET /admin/listings/{id} — detail page
# ---------------------------------------------------------------------------


@router.get("/{listing_id}", response_class=HTMLResponse)
def listing_detail_ui(
    listing_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Render the detail page with composite preview + edit form + actions."""
    _check_token(x_admin_token, request, admin_token)

    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    template = db.get(Template, row.template_id) if row.template_id else None
    listing = _row_to_view(row, template)

    # Template's available sizes/colors for edit form
    opts = {}
    if template:
        try:
            opts = json.loads(template.variation_options_json or "{}")
        except (json.JSONDecodeError, TypeError):
            opts = {}

    return get_jinja().TemplateResponse(
        request,
        "listings/detail.html",
        {
            "listing": listing,
            "template": template,
            "template_opts": opts,
            "draft_url": (
                listing_creator_service._draft_url(row.etsy_listing_id)
                if row.etsy_listing_id else None
            ),
        },
    )


# ---------------------------------------------------------------------------
# PUT /admin/listings/{id} — edit (draft local-only, live write-through Etsy)
# ---------------------------------------------------------------------------


@router.put("/{listing_id}")
def edit_listing(
    listing_id: int,
    request: Request,
    body: dict = Body(...),
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Edit fields. Draft: DB only. Live: DB + Etsy update_listing."""
    _check_token(x_admin_token, request, admin_token)

    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if row.deleted_at is not None:
        raise HTTPException(status_code=410, detail="Listing was deleted")

    try:
        payload = json.loads(row.local_payload_json or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}

    # Merge incoming fields into local_payload (allow partial update)
    changed_text: dict = {}
    if "title" in body:
        payload["title"] = body["title"]
        row.original_title = body["title"]
        changed_text["title"] = body["title"]
    if "description" in body:
        payload["description"] = body["description"]
        row.original_desc = body["description"]
        changed_text["description"] = body["description"]
    if "tags" in body:
        tags = list(body["tags"] or [])
        payload["tags"] = tags
        row.original_tags = json.dumps(tags)
        changed_text["tags"] = tags
    if "enabled_combos" in body:
        payload["enabled_combos"] = body["enabled_combos"]
    if "zone_designs" in body:
        payload["zone_designs"] = body["zone_designs"] or {}

    row.local_payload_json = json.dumps(payload)
    db.commit()
    db.refresh(row)

    # Live mode → write-through to Etsy for text fields
    etsy_synced = False
    if row.status == "created" and row.etsy_listing_id and changed_text:
        try:
            with EtsyApiClient(db) as client:
                client.update_listing(row.etsy_listing_id, **changed_text)
            etsy_synced = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Etsy update_listing failed for listing %d: %s", listing_id, exc
            )
            return JSONResponse({
                "ok": False,
                "error": f"Etsy update failed: {exc}",
                "etsy_synced": False,
            }, status_code=502)

    return JSONResponse({
        "ok": True,
        "status": row.status,
        "etsy_synced": etsy_synced,
    })


# ---------------------------------------------------------------------------
# POST /admin/listings/{id}/upload — push draft to Etsy
# ---------------------------------------------------------------------------


@router.post("/{listing_id}/upload")
def upload_listing(
    listing_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Trigger upload_to_etsy. Synchronous (caller waits ~30s)."""
    _check_token(x_admin_token, request, admin_token)

    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    shop_id = _resolve_shop_id()
    try:
        result = listing_creator_service.upload_to_etsy(db, listing_id, shop_id)
    except listing_creator_service.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("upload_to_etsy failed for listing %d", listing_id)
        raise HTTPException(status_code=502, detail=f"Upload failed: {exc}") from exc
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /admin/listings/{id}/sync — pull state from Etsy
# ---------------------------------------------------------------------------


@router.post("/{listing_id}/sync")
def sync_listing(
    listing_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Pull current state from Etsy and overwrite local fields."""
    _check_token(x_admin_token, request, admin_token)

    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not row.etsy_listing_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot sync a draft (no etsy_listing_id). Upload first.",
        )

    row.status = "syncing"
    db.commit()
    try:
        with EtsyApiClient(db) as client:
            etsy_data = client.get_listing(row.etsy_listing_id)
        # Map Etsy fields → local
        row.original_title = etsy_data.get("title") or row.original_title
        row.original_desc = etsy_data.get("description") or row.original_desc
        if "tags" in etsy_data:
            row.original_tags = json.dumps(etsy_data.get("tags") or [])
        # Etsy state 'inactive' / 'sold_out' / 'draft' → local mapping
        etsy_state = etsy_data.get("state")
        if etsy_state == "inactive":
            row.status = "deleted"
            row.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            row.status = "created"
        db.commit()
        db.refresh(row)
    except Exception as exc:
        db.rollback()
        row = db.get(Listing, listing_id)
        if row is not None:
            row.status = "failed"
            row.last_push_error = f"sync failed: {exc}"
            db.commit()
        logger.exception("sync failed for listing %d", listing_id)
        raise HTTPException(status_code=502, detail=f"Sync failed: {exc}") from exc

    return JSONResponse({
        "ok": True,
        "status": row.status,
        "title": row.original_title,
    })


# ---------------------------------------------------------------------------
# POST /admin/listings/{id}/rerender — invalidate cache + re-render composites
# ---------------------------------------------------------------------------


@router.post("/{listing_id}/rerender")
def rerender_listing(
    listing_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Invalidate R2 composite cache and re-render full image pool."""
    _check_token(x_admin_token, request, admin_token)

    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if not row.template_id or not row.design_id:
        raise HTTPException(status_code=400, detail="Listing missing template/design")

    try:
        composite_service.invalidate_composites_for_template(row.template_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("invalidate_composites failed (non-fatal): %s", exc)

    try:
        payload = json.loads(row.local_payload_json or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    zone_designs = payload.get("zone_designs") or None

    rendered = composite_service.render_all_for_listing(
        db, row.template_id, row.design_id, zone_designs=zone_designs
    )
    gallery = sorted(
        [r for r in rendered if r["url"] is not None],
        key=lambda r: r["rank"],
    )[:10]

    payload["gallery_snapshot"] = [
        {"rank": g["rank"], "color": g.get("color"), "url": g["url"], "id": g.get("id")}
        for g in gallery
    ]
    row.local_payload_json = json.dumps(payload)
    row.original_images = json.dumps([g["url"] for g in gallery])
    db.commit()

    return JSONResponse({
        "ok": True,
        "gallery_count": len(gallery),
        "composite_urls": payload["gallery_snapshot"],
    })


# ---------------------------------------------------------------------------
# DELETE /admin/listings/{id} — Etsy DELETE + soft-delete local
# ---------------------------------------------------------------------------


@router.delete("/{listing_id}")
def delete_listing(
    listing_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> JSONResponse:
    """Delete on Etsy (if live) and soft-delete local row."""
    _check_token(x_admin_token, request, admin_token)

    row = db.get(Listing, listing_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    if row.deleted_at is not None:
        return JSONResponse({"deleted": True, "idempotent": True})

    if row.etsy_listing_id:
        try:
            shop_id = _resolve_shop_id()
            with EtsyApiClient(db) as client:
                client.delete_listing(shop_id, row.etsy_listing_id)
        except Exception as exc:
            logger.warning(
                "Etsy delete_listing failed for listing %d (etsy=%s): %s",
                listing_id, row.etsy_listing_id, exc,
            )
            raise HTTPException(status_code=502, detail=f"Etsy delete failed: {exc}") from exc

    row.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.status = "deleted"
    db.commit()
    return JSONResponse({"deleted": True})
