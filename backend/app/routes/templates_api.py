"""Templates JSON API — CRUD endpoints protected by X-Admin-Token."""
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, field_validator
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
    sizes: list[str] = []
    colors: list[str] = []


class TemplateOut(BaseModel):
    id: int
    name: str
    category: str
    base_image_url: str
    composite_anchor: dict[str, float]
    default_price_cents: int
    variation_options: dict[str, Any]
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
    return TemplateOut(
        id=t.id,
        name=t.name,
        category=t.category,
        base_image_url=t.base_image_url,
        composite_anchor=json.loads(t.composite_anchor_json),
        default_price_cents=t.default_price_cents,
        variation_options=json.loads(t.variation_options_json),
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
        fields["variation_options_json"] = json.dumps(body.variation_options.model_dump())

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
