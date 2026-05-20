"""Idea → Listing 3-step wizard router.

Stateless: idea_id is in the URL path; all step state is carried via hidden form
fields between POST transitions. No server-side session required.

Routes:
  GET  /admin/ideas/{idea_id}/create-listing        — Step 1: idea preview + field checkboxes
  POST /admin/ideas/{idea_id}/create-listing/step2  — Step 2: template + design picker
  POST /admin/ideas/{idea_id}/create-listing/step3  — Step 3: final review form
  POST /admin/ideas/{idea_id}/create-listing/submit — Final submission → calls listing_creator_service
"""
from __future__ import annotations

import json
import logging
import time
from io import BytesIO

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.clients.r2_storage_client import R2StorageClient
from app.clients.removebg_client import RemoveBgClient
from app.config import settings
from app.database import get_db
from app.services import design_service, idea_service, listing_creator_service, template_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/ideas", tags=["admin-idea-wizard"])


# ---------------------------------------------------------------------------
# Auth helper (mirrors ideas_admin._check_token / templates_admin._check_token)
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
    from app.main import jinja_templates
    return jinja_templates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _parse_materials(raw: str | None) -> list[str]:
    return _parse_tags(raw)


def _all_combos_from_template(template) -> list[dict]:
    """Build the full enabled_combos list from template variation_options_json."""
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    sizes = opts.get("sizes", [])
    colors = opts.get("colors", [])
    combos = []
    for s in sizes:
        size_name = s["name"] if isinstance(s, dict) else s
        for color in colors:
            combos.append({"size": str(size_name), "color": str(color), "enabled": True})
    return combos


# ---------------------------------------------------------------------------
# Step 1 — GET /admin/ideas/{idea_id}/create-listing
# ---------------------------------------------------------------------------


@router.get("/{idea_id}/create-listing", response_class=HTMLResponse)
def wizard_step1(
    idea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Render Step 1: idea preview + field-selection checkboxes + IP-warning if applicable."""
    _check_token(x_admin_token, request, admin_token)

    idea = idea_service.get_idea(db, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    tags = _parse_tags(idea.tags_json)
    materials = _parse_materials(idea.materials_json)
    show_ip_warning = bool(idea.source == "extension_passive" or idea.reference_image_url)

    return _get_jinja().TemplateResponse(
        request,
        "wizard/step1.html",
        {
            "idea": idea,
            "tags": tags,
            "materials": materials,
            "show_ip_warning": show_ip_warning,
            "price_dollars": (idea.price_cents / 100) if idea.price_cents else None,
        },
    )


# ---------------------------------------------------------------------------
# Step 2 — POST /admin/ideas/{idea_id}/create-listing/step2
# ---------------------------------------------------------------------------


@router.post("/{idea_id}/create-listing/step2", response_class=HTMLResponse)
async def wizard_step2(
    idea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Render Step 2: template dropdown + design picker (existing or inline upload).

    Receives Step 1 checkbox flags; passes them forward as hidden fields.
    """
    _check_token(x_admin_token, request, admin_token)

    idea = idea_service.get_idea(db, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    form = await request.form()

    # Collect prefill flags (checkbox = present means checked/true)
    prefill_flags = {
        "title":       form.get("prefill_title") == "on",
        "tags":        form.get("prefill_tags") == "on",
        "description": form.get("prefill_description") == "on",
        "materials":   form.get("prefill_materials") == "on",
        "taxonomy":    form.get("prefill_taxonomy") == "on",
        "who_made":    form.get("prefill_who_made") == "on",
        "when_made":   form.get("prefill_when_made") == "on",
        "price":       form.get("prefill_price") == "on",
    }

    # Compute prefilled values for later steps
    tags = _parse_tags(idea.tags_json)
    materials = _parse_materials(idea.materials_json)

    prefilled = {
        "title":       idea.title or "" if prefill_flags["title"] else "",
        "tags_csv":    ", ".join(tags) if prefill_flags["tags"] else "",
        "description": idea.description or "" if prefill_flags["description"] else "",
        "materials_csv": ", ".join(materials) if prefill_flags["materials"] else "",
        "taxonomy_id": str(idea.taxonomy_id or "") if prefill_flags["taxonomy"] else "",
        "who_made":    idea.who_made or "" if prefill_flags["who_made"] else "",
        "when_made":   idea.when_made or "" if prefill_flags["when_made"] else "",
        "price":       str(idea.price_cents / 100) if (prefill_flags["price"] and idea.price_cents) else "",
    }

    all_templates = template_service.list_templates(db)
    all_designs = [d for d in design_service.list_designs(db) if d.source_type != "reference_only"]

    return _get_jinja().TemplateResponse(
        request,
        "wizard/step2.html",
        {
            "idea": idea,
            "prefill_flags": prefill_flags,
            "prefilled": prefilled,
            "templates": all_templates,
            "designs": all_designs,
        },
    )


# ---------------------------------------------------------------------------
# Step 3 — POST /admin/ideas/{idea_id}/create-listing/step3
# ---------------------------------------------------------------------------


@router.post("/{idea_id}/create-listing/step3", response_class=HTMLResponse)
async def wizard_step3(
    idea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> HTMLResponse:
    """Render Step 3: final review form with all fields editable.

    Handles optional inline design upload from Step 2 if upload_design file present.
    Carries all state forward as hidden or prefilled form fields.
    """
    _check_token(x_admin_token, request, admin_token)

    idea = idea_service.get_idea(db, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    form = await request.form()

    # Resolve template_id
    try:
        template_id = int(form.get("template_id") or 0)
    except (TypeError, ValueError):
        template_id = 0

    if not template_id:
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {"error": "No template selected. Please go back and pick a template.", "idea": idea},
            status_code=422,
        )

    # Handle optional inline design upload
    design_id_raw = form.get("design_id") or ""
    upload_file: UploadFile | None = form.get("upload_design")  # type: ignore[assignment]

    design_id = 0
    upload_error: str | None = None

    if upload_file and getattr(upload_file, "filename", None):
        # User uploaded a new design
        try:
            file_bytes = await upload_file.read()
            design_name = form.get("upload_design_name") or upload_file.filename or "Uploaded design"
            new_design = design_service.create_design(
                session=db,
                file_bytes=file_bytes,
                name=str(design_name),
                source_type="upload",
            )
            design_id = new_design.id
        except Exception as exc:  # noqa: BLE001
            upload_error = f"Design upload failed: {exc}"
    else:
        try:
            design_id = int(design_id_raw)
        except (TypeError, ValueError):
            design_id = 0

    if upload_error:
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {"error": upload_error, "idea": idea},
            status_code=422,
        )

    if not design_id:
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {"error": "No design selected or uploaded. Please go back and pick a design.", "idea": idea},
            status_code=422,
        )

    # Carry forward prefilled text values
    prefilled = {
        "title":        form.get("title") or "",
        "tags_csv":     form.get("tags_csv") or "",
        "description":  form.get("description") or "",
        "materials_csv": form.get("materials_csv") or "",
        "taxonomy_id":  form.get("taxonomy_id") or "",
        "who_made":     form.get("who_made") or "",
        "when_made":    form.get("when_made") or "",
        "price":        form.get("price") or "",
    }

    # Default to the shop connected via OAuth; fall back to (no-op) settings shim.
    from app.clients.etsy_oauth import get_default_shop_id
    default_shop_id = get_default_shop_id(db) or getattr(settings, "etsy_shop_id", "") or ""

    # Fetch template + design for review display
    tmpl = template_service.get_template(db, template_id)
    design = design_service.get_design(db, design_id)

    return _get_jinja().TemplateResponse(
        request,
        "wizard/step3.html",
        {
            "idea": idea,
            "template": tmpl,
            "design": design,
            "template_id": template_id,
            "design_id": design_id,
            "prefilled": prefilled,
            "default_shop_id": default_shop_id,
        },
    )


# ---------------------------------------------------------------------------
# Submit — POST /admin/ideas/{idea_id}/create-listing/submit
# ---------------------------------------------------------------------------


@router.post("/{idea_id}/create-listing/submit", response_class=HTMLResponse)
async def wizard_submit(
    idea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> Response:
    """Final submission: validate → call listing_creator_service → link idea → render result."""
    _check_token(x_admin_token, request, admin_token)

    idea = idea_service.get_idea(db, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")

    form = await request.form()

    # --- Parse and validate form fields ---
    try:
        template_id = int(form.get("template_id") or 0)
        design_id = int(form.get("design_id") or 0)
    except (TypeError, ValueError):
        template_id = design_id = 0

    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    tags_csv = form.get("tags_csv") or ""
    tags = [t.strip() for t in tags_csv.split(",") if t.strip()][:13]
    shop_id = (form.get("shop_id") or "").strip()
    if not shop_id:
        from app.clients.etsy_oauth import get_default_shop_id
        shop_id = get_default_shop_id(db) or ""

    # Validation
    errors: list[str] = []
    if not template_id:
        errors.append("Template is required")
    if not design_id:
        errors.append("Design is required")
    if not title:
        errors.append("Title is required")
    elif len(title) > 140:
        errors.append("Title must be ≤ 140 characters")
    if not description:
        errors.append("Description is required")
    if not shop_id:
        errors.append("Shop ID is required (connect an Etsy shop via /auth/etsy/start, or enter it manually)")
    if len(tags) > 13:
        errors.append("Maximum 13 tags allowed")

    if errors:
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {"error": " | ".join(errors), "idea": idea},
            status_code=422,
        )

    # Build enabled_combos: use all combos from the template (default = all enabled)
    tmpl = template_service.get_template(db, template_id)
    if tmpl is None:
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {"error": f"Template {template_id} not found", "idea": idea},
            status_code=404,
        )

    enabled_combos = _all_combos_from_template(tmpl)
    if not enabled_combos:
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {
                "error": "Template has no size/color combinations configured. "
                         "Please add sizes and colors to the template first.",
                "idea_id": idea_id,
            },
            status_code=422,
        )

    # --- v0.10: save_draft only — wizard no longer pushes to Etsy here.
    # User reviews the rendered composite on /admin/listings/{id} then clicks
    # the Upload button to call upload_to_etsy. shop_id is therefore irrelevant
    # at this stage (passed through only for legacy back-compat callers).
    try:
        result = listing_creator_service.save_draft(
            session=db,
            template_id=template_id,
            design_id=design_id,
            title=title,
            description=description,
            tags=tags,
            enabled_combos=enabled_combos,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "Wizard save_draft failed for idea=%d template=%d design=%d",
            idea_id, template_id, design_id,
        )
        db.rollback()
        return _get_jinja().TemplateResponse(
            request,
            "wizard/_error.html",
            {"error": str(exc), "idea_id": idea_id},
            status_code=500,
        )

    listing_id: int = result["listing_id"]

    # --- Post-creation: link idea to listing + mark drafted ---
    try:
        idea_service.link_to_listing(db, idea_id, listing_id)
        idea_service.mark_drafted(db, idea_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Post-save idea link/status update failed for idea=%d listing=%d: %s",
            idea_id, listing_id, exc,
        )

    # Redirect to detail page where user can preview + Upload
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=f"/admin/listings/{listing_id}?flash=Draft+saved.+Click+Upload+to+push+to+Etsy.",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Extract design — POST /admin/ideas/{idea_id}/extract-design
# ---------------------------------------------------------------------------


class ExtractDesignBody(BaseModel):
    """Validated body for the extract-design endpoint."""

    crop_box: list[float] = Field(..., min_length=4, max_length=4)

    @field_validator("crop_box")
    @classmethod
    def validate_crop_box(cls, v: list[float]) -> list[float]:
        """Ensure crop_box is [x, y, w, h] with all values in [0,1] and non-zero area."""
        x, y, w, h = v
        for name, val in [("x", x), ("y", y), ("w", w), ("h", h)]:
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"crop_box.{name}={val} must be in [0, 1]")
        if w <= 0 or h <= 0:
            raise ValueError("crop_box width and height must be > 0")
        if x + w > 1.0001 or y + h > 1.0001:
            raise ValueError("crop_box extends beyond image bounds")
        return v


def _download_with_retry(url: str) -> bytes:
    """Download *url* with 10 s timeout; retry once on timeout or 5xx.

    Returns:
        Raw image bytes.

    Raises:
        HTTPException(503): on persistent failure.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; EtsyAuto/0.8; +https://etsyauto.app)"}
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
                resp = client.get(url)
                if resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                    continue
                resp.raise_for_status()
                return resp.content
        except httpx.TimeoutException as exc:
            last_exc = exc
            logger.warning("Download timeout attempt %d for %s", attempt + 1, url)
    raise HTTPException(status_code=503, detail=f"Image download failed: {last_exc}")


def _crop_to_png(img_bytes: bytes, crop_box: list[float]) -> bytes:
    """Crop *img_bytes* to the fractional region *crop_box* and return PNG bytes.

    Args:
        img_bytes: Raw source image bytes (any PIL-supported format).
        crop_box: [x, y, w, h] in 0-1 fractions of image dimensions.

    Returns:
        PNG bytes of the cropped region.

    Raises:
        ValueError: If the resulting crop is smaller than 10×10 px.
    """
    with Image.open(BytesIO(img_bytes)) as img:
        w_px, h_px = img.size
        x, y, w, h = crop_box
        left = int(x * w_px)
        top = int(y * h_px)
        right = int((x + w) * w_px)
        bottom = int((y + h) * h_px)
        cropped = img.crop((left, top, right, bottom))
        crop_w, crop_h = cropped.size
        if crop_w < 10 or crop_h < 10:
            raise ValueError(
                f"Cropped region too small: {crop_w}×{crop_h} px (minimum 10×10)"
            )
        buf = BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()


@router.post("/{idea_id}/extract-design")
async def extract_design(
    idea_id: int,
    body: ExtractDesignBody,
    db: Session = Depends(get_db),
    request: Request = None,  # type: ignore[assignment]
    x_admin_token: str | None = Header(default=None),
    admin_token: str | None = Cookie(default=None),
) -> dict:
    """Crop idea reference image, remove background, save as derivative Design.

    Returns:
        {"design_id": int, "file_url": str}
    """
    _check_token(x_admin_token, request, admin_token)

    idea = idea_service.get_idea(db, idea_id)
    if idea is None:
        raise HTTPException(status_code=404, detail="Idea not found")
    if not idea.reference_image_url:
        raise HTTPException(status_code=400, detail="Idea has no reference_image_url")

    # Download source image (10 s timeout, 1 retry)
    img_bytes = _download_with_retry(idea.reference_image_url)

    # PIL crop to selected region — raises ValueError → 422
    try:
        cropped_bytes = _crop_to_png(img_bytes, body.crop_box)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Strip background via remove.bg
    try:
        rbg = RemoveBgClient()
        cutout_bytes = rbg.remove_bg(cropped_bytes)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Background removal failed: {exc}") from exc

    r2 = R2StorageClient()

    # Idempotent upsert: reuse existing derivative Design row if present
    if idea.design_id:
        existing = design_service.get_design(db, idea.design_id)
        if existing and existing.source_type == "derivative":
            # Derive old R2 key from public URL so we can delete it
            public_prefix = settings.r2_public_url.rstrip("/") + "/"
            if existing.file_url.startswith(public_prefix):
                old_key = existing.file_url[len(public_prefix):]
                r2.delete_object(old_key)

            new_key = f"designs/derivative/{existing.id}-{int(time.time())}.png"
            try:
                file_url = r2.upload_image(cutout_bytes, new_key)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail=f"R2 upload failed: {exc}") from exc

            design_service.update_design_file(db, existing.id, new_key, file_url)
            idea_service.set_design_id(db, idea_id, existing.id)
            return {"design_id": existing.id, "file_url": file_url}

    # Create new derivative Design row (validate PNG + upload inside create_design)
    try:
        design = design_service.create_design(
            session=db,
            file_bytes=cutout_bytes,
            name=f"Derivative from idea #{idea_id}",
            source_type="derivative",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Design creation failed: {exc}") from exc

    idea_service.set_design_id(db, idea_id, design.id)
    return {"design_id": design.id, "file_url": design.file_url}
