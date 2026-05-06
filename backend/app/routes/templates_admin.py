"""Templates admin HTML router — Jinja2 form UI protected by X-Admin-Token."""
import json
import logging

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services import design_service, template_service, variation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/templates", tags=["admin-templates"])
designs_router = APIRouter(prefix="/admin/designs", tags=["admin-designs"])
composite_router = APIRouter(prefix="/admin/composite", tags=["admin-composite"])

# Templates dir resolved by main.py when it mounts Jinja2Templates; we import the
# shared instance via a lazy attribute so circular imports are avoided.
_jinja: Jinja2Templates | None = None


def get_jinja() -> Jinja2Templates:
    global _jinja  # noqa: PLW0603
    if _jinja is None:
        from app.main import jinja_templates  # imported lazily to avoid circular
        _jinja = jinja_templates
    return _jinja


MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


def _check_token(x_admin_token: str | None) -> None:
    """Raise 401 HTML-friendly if token missing/wrong."""
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token")


@router.get("", response_class=HTMLResponse)
def list_templates_ui(
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> HTMLResponse:
    """Render the template list page."""
    _check_token(x_admin_token)
    templates = template_service.list_templates(db)
    templates_data = []
    for t in templates:
        templates_data.append({
            "id": t.id,
            "name": t.name,
            "category": t.category,
            "base_image_url": t.base_image_url,
            "default_price_cents": t.default_price_cents,
            "variation_count": template_service.get_variation_count(db, t.id),
        })
    jinja = get_jinja()
    return jinja.TemplateResponse(
        request,
        "templates/list.html",
        {"templates": templates_data},
    )


@router.get("/new", response_class=HTMLResponse)
def new_template_form(
    request: Request,
    x_admin_token: str | None = Header(default=None),
) -> HTMLResponse:
    """Render the new template upload form."""
    _check_token(x_admin_token)
    jinja = get_jinja()
    return jinja.TemplateResponse(request, "templates/form.html", {"error": None})


@router.post("", response_model=None)
async def create_template_ui(
    request: Request,
    name: str = Form(...),
    category: str = Form(...),
    composite_anchor_x: float = Form(...),
    composite_anchor_y: float = Form(...),
    composite_anchor_w: float = Form(...),
    composite_anchor_h: float = Form(...),
    default_price_cents: int = Form(0),
    variation_options_json: str = Form("{}"),
    base_image: UploadFile = File(...),
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> Response:
    """Handle template upload form submission."""
    _check_token(x_admin_token)
    jinja = get_jinja()

    # Validate anchor values
    anchor_vals = [composite_anchor_x, composite_anchor_y, composite_anchor_w, composite_anchor_h]
    if any(not (0.0 <= v <= 1.0) for v in anchor_vals):
        return jinja.TemplateResponse(
            request,
            "templates/form.html",
            {"error": "Anchor values must be between 0 and 1"},
            status_code=422,
        )

    anchor_dict = {"x": composite_anchor_x, "y": composite_anchor_y,
                   "w": composite_anchor_w, "h": composite_anchor_h}

    try:
        variation_options = json.loads(variation_options_json)
    except Exception:
        return jinja.TemplateResponse(
            request,
            "templates/form.html",
            {"error": "Invalid variation_options JSON"},
            status_code=422,
        )

    image_bytes = await base_image.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        return jinja.TemplateResponse(
            request,
            "templates/form.html",
            {"error": "Image exceeds 10 MB limit"},
            status_code=413,
        )

    try:
        template_service.create_template(
            session=db,
            name=name,
            category=category,
            image_bytes=image_bytes,
            anchor_dict=anchor_dict,
            default_price_cents=default_price_cents,
            variation_options=variation_options,
            image_filename=base_image.filename or "template.png",
        )
    except Exception as exc:
        logger.exception("Admin UI: failed to create template")
        return jinja.TemplateResponse(
            request,
            "templates/form.html",
            {"error": str(exc)},
            status_code=500,
        )

    return RedirectResponse(url="/admin/templates", status_code=303)


@router.delete("/{template_id}", status_code=200)
def delete_template_htmx(
    template_id: int,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> dict:
    """HTMX DELETE endpoint — returns empty 200 for row removal."""
    _check_token(x_admin_token)
    try:
        template_service.delete_template(db, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {}


@router.post("/{template_id}/delete", response_model=None)
def delete_template_form(
    template_id: int,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> Response:
    """HTML form POST delete (browsers can't send DELETE natively)."""
    _check_token(x_admin_token)
    try:
        template_service.delete_template(db, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/templates", status_code=303)


# ---------------------------------------------------------------------------
# Variations matrix UI
# ---------------------------------------------------------------------------

def _build_matrix_context(db: Session, template_id: int) -> dict:
    """Build template data + matrix lookup dict for the variations view."""
    tmpl = template_service.get_template(db, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template not found")

    opts = json.loads(tmpl.variation_options_json) if tmpl.variation_options_json else {}
    sizes: list[str] = opts.get("sizes", [])
    colors: list[str] = opts.get("colors", [])

    existing = variation_service.list_variations(db, template_id)
    # matrix lookup: "size||color" → {"price_cents": ..., "sku": ...}
    matrix: dict[str, dict] = {
        f"{v.size}||{v.color}": {"price_cents": v.price_cents, "sku": v.sku}
        for v in existing
    }

    return {
        "template": tmpl,
        "sizes": sizes,
        "colors": colors,
        "matrix": matrix,
        "variations": existing,
    }


@router.get("/{template_id}/variations", response_class=HTMLResponse)
def variations_matrix_ui(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> HTMLResponse:
    """Render the variations matrix grid for a template."""
    _check_token(x_admin_token)
    ctx = _build_matrix_context(db, template_id)
    jinja = get_jinja()
    return jinja.TemplateResponse(
        request,
        "templates/variations.html",
        {**ctx, "error": None, "success": None},
    )


@router.post("/{template_id}/variations", response_model=None)
async def save_variations_ui(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> Response:
    """Handle bulk save of variation matrix from HTML form POST."""
    _check_token(x_admin_token)
    jinja = get_jinja()

    tmpl = template_service.get_template(db, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template not found")

    opts = json.loads(tmpl.variation_options_json) if tmpl.variation_options_json else {}
    sizes: list[str] = opts.get("sizes", [])
    colors: list[str] = opts.get("colors", [])

    form_data = await request.form()

    # Reconstruct variations from flat form fields: size_{i}_{j}, color_{i}_{j}, price_{i}_{j}, sku_{i}_{j}
    variations: list[dict] = []
    for i, size in enumerate(sizes):
        for j, color in enumerate(colors):
            size_val = form_data.get(f"size_{i}_{len(colors) - 1 - j}", size)
            color_val = form_data.get(f"color_{i}_{len(colors) - 1 - j}", color)
            price_raw = form_data.get(f"price_{i}_{len(colors) - 1 - j}", "0")
            sku_raw = form_data.get(f"sku_{i}_{len(colors) - 1 - j}", "") or None

            try:
                price_cents = int(price_raw)
                if price_cents < 0:
                    raise ValueError("negative price")
            except (ValueError, TypeError):
                price_cents = tmpl.default_price_cents

            variations.append({
                "size": str(size_val),
                "color": str(color_val),
                "price_cents": price_cents,
                "sku": str(sku_raw).strip() if sku_raw else None,
            })

    if len(variations) > 30:
        ctx = _build_matrix_context(db, template_id)
        return jinja.TemplateResponse(
            request,
            "templates/variations.html",
            {**ctx, "error": f"Too many variations ({len(variations)}). Max 30 allowed.", "success": None},
            status_code=400,
        )

    try:
        variation_service.replace_variations(db, template_id, variations)
    except IntegrityError:
        db.rollback()
        ctx = _build_matrix_context(db, template_id)
        return jinja.TemplateResponse(
            request,
            "templates/variations.html",
            {**ctx, "error": "Duplicate (size, color) combination detected.", "success": None},
            status_code=409,
        )
    except Exception as exc:
        logger.exception("Admin UI: failed to save variations for template %d", template_id)
        ctx = _build_matrix_context(db, template_id)
        return jinja.TemplateResponse(
            request,
            "templates/variations.html",
            {**ctx, "error": str(exc), "success": None},
            status_code=500,
        )

    return RedirectResponse(
        url=f"/admin/templates/{template_id}/variations", status_code=303
    )


# ---------------------------------------------------------------------------
# Designs admin UI
# ---------------------------------------------------------------------------

@designs_router.get("", response_class=HTMLResponse)
def list_designs_ui(
    request: Request,
    source_type: str | None = None,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> HTMLResponse:
    """Render the design library page with thumbnails and filter."""
    _check_token(x_admin_token)
    designs = design_service.list_designs(db, source_type=source_type)
    jinja = get_jinja()
    return jinja.TemplateResponse(
        request,
        "templates/designs-list.html",
        {"designs": designs, "source_type_filter": source_type or ""},
    )


@designs_router.post("", response_model=None)
async def upload_design_ui(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    source_type: str = Form("upload"),
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> Response:
    """Handle design upload form submission."""
    _check_token(x_admin_token)
    jinja = get_jinja()
    file_bytes = await file.read()
    try:
        design_service.create_design(session=db, file_bytes=file_bytes, name=name, source_type=source_type)
    except ValueError as exc:
        designs = design_service.list_designs(db)
        return jinja.TemplateResponse(
            request,
            "templates/designs-list.html",
            {"designs": designs, "source_type_filter": "", "error": str(exc)},
            status_code=400,
        )
    except Exception as exc:
        logger.exception("Admin UI: failed to upload design")
        designs = design_service.list_designs(db)
        return jinja.TemplateResponse(
            request,
            "templates/designs-list.html",
            {"designs": designs, "source_type_filter": "", "error": str(exc)},
            status_code=500,
        )
    return RedirectResponse(url="/admin/designs", status_code=303)


@designs_router.post("/{design_id}/delete", response_model=None)
def delete_design_ui(
    design_id: int,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> Response:
    """HTML form POST delete for designs."""
    _check_token(x_admin_token)
    try:
        design_service.delete_design(db, design_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RedirectResponse(url="/admin/designs", status_code=303)


# ---------------------------------------------------------------------------
# Composite preview admin UI
# ---------------------------------------------------------------------------

@composite_router.get("", response_class=HTMLResponse)
def composite_preview_ui(
    request: Request,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
) -> HTMLResponse:
    """Render composite preview page with template and design dropdowns."""
    _check_token(x_admin_token)
    templates = template_service.list_templates(db)
    # Exclude reference_only designs from composite dropdown
    designs = design_service.list_designs(db)
    compositable = [d for d in designs if d.source_type != "reference_only"]
    jinja = get_jinja()
    return jinja.TemplateResponse(
        request,
        "templates/composite-preview.html",
        {"templates": templates, "designs": compositable, "composite_url": None, "error": None},
    )
