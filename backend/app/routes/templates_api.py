"""Templates JSON API — CRUD endpoints protected by X-Admin-Token."""
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin_token
from app.services import template_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class CompositeAnchor(BaseModel):
    x: float
    y: float
    w: float
    h: float

    @field_validator("x", "y", "w", "h")
    @classmethod
    def _clamp_0_1(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("composite_anchor values must be between 0 and 1")
        return v


class VariationOptions(BaseModel):
    """Loosely-typed convention for template variation options.

    `sizes` accepts either legacy list[str] (v0.2.0) or list[{name, price_cents}] (v0.4.0+).
    `primary_color` and `etsy_taxonomy_id` are used by the Listing Creator (sub-feature C).
    Extra keys (e.g. shipping_profile_id, readiness_state_id, item_weight) are
    preserved through PUT round-trips so the admin UI can save them.
    """

    model_config = ConfigDict(extra="allow")

    sizes: list[Any] = []
    colors: list[str] = []
    primary_color: str | None = None
    etsy_taxonomy_id: int | None = None


class TemplateOut(BaseModel):
    id: int
    name: str
    category: str
    base_image_url: str
    composite_anchor: dict[str, float]
    default_price_cents: int
    variation_options: dict[str, Any]
    color_base_images: dict[str, str] = {}
    variation_count: int = 0

    model_config = {"from_attributes": True}


class TemplateUpdateIn(BaseModel):
    name: str | None = None
    category: str | None = None
    composite_anchor: CompositeAnchor | None = None
    default_price_cents: int | None = None
    variation_options: VariationOptions | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_anchor(raw: str) -> CompositeAnchor:
    try:
        data = json.loads(raw)
        return CompositeAnchor(**data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid composite_anchor JSON: {exc}") from exc


def _parse_variation_options(raw: str) -> VariationOptions:
    try:
        data = json.loads(raw)
        return VariationOptions(**data)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid variation_options JSON: {exc}") from exc


def _to_template_out(t: Any, db: Session) -> TemplateOut:
    try:
        color_bases = json.loads(getattr(t, "color_base_images_json", None) or "{}")
    except (json.JSONDecodeError, TypeError):
        color_bases = {}
    return TemplateOut(
        id=t.id,
        name=t.name,
        category=t.category,
        base_image_url=t.base_image_url,
        composite_anchor=json.loads(t.composite_anchor_json),
        default_price_cents=t.default_price_cents,
        variation_options=json.loads(t.variation_options_json),
        color_base_images=color_bases,
        variation_count=template_service.get_variation_count(db, t.id),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", status_code=201, dependencies=[Depends(require_admin_token)])
async def create_template(
    name: str = Form(...),
    category: str = Form(...),
    composite_anchor: str = Form(...),
    default_price_cents: int = Form(0),
    variation_options: str = Form("{}"),
    base_image: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> TemplateOut:
    """Upload a new template with base image."""
    anchor = _parse_anchor(composite_anchor)
    opts = _parse_variation_options(variation_options)

    image_bytes = await base_image.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB limit")

    try:
        tmpl = template_service.create_template(
            session=db,
            name=name,
            category=category,
            image_bytes=image_bytes,
            anchor_dict=anchor.model_dump(),
            default_price_cents=default_price_cents,
            variation_options=opts.model_dump(),
            image_filename=base_image.filename or "template.png",
        )
    except Exception as exc:
        logger.exception("Failed to create template")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _to_template_out(tmpl, db)


@router.get("", dependencies=[Depends(require_admin_token)])
def list_templates(db: Session = Depends(get_db)) -> list[TemplateOut]:
    """List all templates with variation counts."""
    templates = template_service.list_templates(db)
    return [_to_template_out(t, db) for t in templates]


@router.get("/{template_id}", dependencies=[Depends(require_admin_token)])
def get_template(template_id: int, db: Session = Depends(get_db)) -> TemplateOut:
    """Get a single template by id."""
    tmpl = template_service.get_template(db, template_id)
    if tmpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return _to_template_out(tmpl, db)


@router.put("/{template_id}", dependencies=[Depends(require_admin_token)])
def update_template(
    template_id: int,
    body: TemplateUpdateIn,
    db: Session = Depends(get_db),
) -> TemplateOut:
    """Update mutable template fields."""
    fields: dict[str, Any] = {}
    if body.name is not None:
        fields["name"] = body.name
    if body.category is not None:
        fields["category"] = body.category
    if body.composite_anchor is not None:
        fields["composite_anchor_json"] = json.dumps(body.composite_anchor.model_dump())
    if body.default_price_cents is not None:
        fields["default_price_cents"] = body.default_price_cents
    if body.variation_options is not None:
        # Etsy hard cap: sizes × colors ≤ 30 inventory rows per listing. Reject
        # eagerly so the admin UI surfaces the error before listing-creator runs.
        vo = body.variation_options.model_dump()
        n_sizes = len(vo.get("sizes") or [])
        n_colors = len(vo.get("colors") or [])
        if n_sizes * n_colors > 30:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"sizes × colors = {n_sizes} × {n_colors} = {n_sizes * n_colors} "
                    f"exceeds Etsy cap of 30 inventory rows per listing."
                ),
            )
        fields["variation_options_json"] = json.dumps(vo)

    try:
        tmpl = template_service.update_template(db, template_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return _to_template_out(tmpl, db)


@router.delete("/{template_id}", status_code=204, dependencies=[Depends(require_admin_token)])
def delete_template(template_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a template and cascade its variations; also removes R2 base image."""
    try:
        template_service.delete_template(db, template_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Per-color base images (sub-feature C)
# ---------------------------------------------------------------------------

@router.post(
    "/{template_id}/color-bases/{color}",
    status_code=200,
    dependencies=[Depends(require_admin_token)],
)
async def upload_color_base(
    template_id: int,
    color: str,
    base_image: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> TemplateOut:
    """Upload a per-color base image for a template.

    Validates that *color* is in the template's variation_options.colors list.
    Replaces existing base for that color (deletes old R2 object best-effort).
    """
    image_bytes = await base_image.read()
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 10 MB limit")

    try:
        tmpl = template_service.set_color_base(
            session=db,
            template_id=template_id,
            color=color,
            image_bytes=image_bytes,
            image_filename=base_image.filename or "color-base.png",
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "not found" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as exc:
        logger.exception("Failed to set color base for template %d color=%s", template_id, color)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _to_template_out(tmpl, db)


@router.delete(
    "/{template_id}/color-bases/{color}",
    status_code=204,
    dependencies=[Depends(require_admin_token)],
)
def delete_color_base(
    template_id: int,
    color: str,
    db: Session = Depends(get_db),
) -> None:
    """Remove a per-color base image. Idempotent (404 only when template missing)."""
    try:
        template_service.delete_color_base(db, template_id, color)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
