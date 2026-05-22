"""Product mockup admin route — Jinja2 UI for generating product mockups via genmockup pipeline."""
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import require_admin_token
from app.services.product_mockup_service import (
    generate_product_mockups,
    list_available_products,
    reload_registry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/product-mockup", tags=["admin-product-mockup"])

_jinja: Jinja2Templates | None = None

_MAX_ARTWORK_BYTES = 20 * 1024 * 1024  # 20 MB

_VALID_PRODUCTS = {"tshirt", "poster", "canvas", "pillow"}


def _get_jinja() -> Jinja2Templates:
    global _jinja  # noqa: PLW0603
    if _jinja is None:
        from app.main import jinja_templates
        _jinja = jinja_templates
    return _jinja


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, _: None = Depends(require_admin_token)):
    return _get_jinja().TemplateResponse(
        request, "mockup/index.html",
        {"products": list_available_products(), "results": [], "error": None},
    )


@router.get("/catalog")
async def catalog(_: None = Depends(require_admin_token)):
    return JSONResponse({"products": list_available_products()})


@router.post("/reload")
async def reload(_: None = Depends(require_admin_token)):
    """Force-reload template registry (after adding new templates to templates_catalog)."""
    count = reload_registry()
    return JSONResponse({"templates_loaded": count})


@router.post("/generate", response_class=HTMLResponse)
async def generate(
    request: Request,
    _: None = Depends(require_admin_token),
    artwork: UploadFile = File(...),
    products: list[str] = Form(...),
):
    invalid = [p for p in products if p not in _VALID_PRODUCTS]
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown product types: {invalid}")

    artwork_bytes = await artwork.read()
    if len(artwork_bytes) > _MAX_ARTWORK_BYTES:
        raise HTTPException(status_code=413, detail="Artwork file too large (max 20MB)")

    error: str | None = None
    results: list[str] = []
    try:
        results = generate_product_mockups(artwork_bytes, products)  # type: ignore[arg-type]
    except Exception:
        logger.exception("Product mockup generation failed")
        error = "Mockup generation failed — check server logs for details."

    return _get_jinja().TemplateResponse(
        request, "mockup/index.html",
        {"products": list_available_products(), "results": results, "error": error},
    )
