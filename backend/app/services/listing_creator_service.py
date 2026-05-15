"""Listing Creator orchestrator — turns (template + design) into an Etsy draft listing.

Steps (idempotent on Listing.template_id+design_id):
    1. Validate template + design + enabled_combos
    2. Idempotency check: existing Listing for (template_id, design_id) → return as-is
    3. Render full image pool via composite_service.render_all_for_listing (parallel)
    4. Resolve Etsy property value IDs (color, size) via taxonomy lookup
    5. Etsy create draft listing
    6. Etsy PUT inventory with full variations matrix
    7. Upload top-10 gallery images sorted by rank (200ms gap), capture etsy_image_id
    7b. Bind per-color variation hero images via set_variation_images (single call)
    8. Persist Listing(etsy_listing_id, template_id, design_id, status='created')
"""
from __future__ import annotations

import json
import logging
import time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.etsy_api_client import EtsyApiClient
from app.config import settings
from app.models.design import Design
from app.models.listing import Listing
from app.models.template import Template
from app.services import composite_service, etsy_taxonomy, listing_pre_check

logger = logging.getLogger(__name__)

# Etsy max 30 inventory rows per listing (also enforced upstream by variation_service)
MAX_VARIATIONS = 30
# Sleep between sequential image uploads to stay below Etsy 5 req/s soft limit.
IMAGE_UPLOAD_GAP_SEC = 0.2


def _existing_listing(
    session: Session, template_id: int, design_id: int
) -> Listing | None:
    """Return a Listing row already created from this (template, design), or None."""
    stmt = select(Listing).where(
        Listing.template_id == template_id, Listing.design_id == design_id
    )
    return session.scalars(stmt).first()


def _validate_combos(template: Template, enabled_combos: list[dict]) -> list[dict]:
    """Validate combos belong to template's variation_options. Return only enabled ones."""
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}

    valid_sizes = {s["name"] if isinstance(s, dict) else s for s in opts.get("sizes", [])}
    valid_colors = {c.strip().title() for c in opts.get("colors", [])}

    enabled: list[dict] = []
    for combo in enabled_combos:
        if not combo.get("enabled", True):
            continue
        size = combo.get("size")
        color = (combo.get("color") or "").strip().title()
        if size not in valid_sizes:
            raise ValueError(f"Combo size {size!r} not in template sizes {sorted(valid_sizes)}")
        if color not in valid_colors:
            raise ValueError(f"Combo color {color!r} not in template colors {sorted(valid_colors)}")
        enabled.append({"size": size, "color": color})

    if not enabled:
        raise ValueError("No enabled combos provided")
    if len(enabled) > MAX_VARIATIONS:
        raise ValueError(f"Too many enabled combos: {len(enabled)} > {MAX_VARIATIONS}")
    return enabled


def _size_price_map(template: Template) -> dict[str, int]:
    """Extract {size_name: price_cents} from template.variation_options_json.

    Accepts both schemas:
      - rich:  [{"name": "S", "price_cents": 1900}, ...]   (preferred, per-size price)
      - plain: ["S", "M", "L"]                              (admin UI default — falls
                                                              back to template.default_price_cents)
    """
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    fallback = int(template.default_price_cents or 0)
    out: dict[str, int] = {}
    for s in opts.get("sizes", []):
        if isinstance(s, dict) and "name" in s:
            out[str(s["name"])] = int(s.get("price_cents", fallback))
        elif isinstance(s, str) and fallback > 0:
            out[s] = fallback
    return out


def create_from_template(
    session: Session,
    *,
    template_id: int,
    design_id: int,
    title: str,
    description: str,
    tags: list[str],
    enabled_combos: list[dict],
    shop_id: str | int,
    quantity_per_variant: int = 100,
    zone_designs: dict[str, int] | None = None,
) -> dict:
    """Create an Etsy draft listing from a template + design.

    Returns:
        {
            "listing_id": int,            # local Listing.id
            "etsy_listing_id": str,
            "draft_url": str,
            "composite_urls": [{"rank": int, "color": str|None, "url": str, "etsy_image_id": int}, ...],
            "variation_images_bound": int,  # count of color→image bindings sent (0 if none)
        }

    Raises:
        ValueError: validation errors (template/design not found, combo mismatch, etc.)
        httpx.HTTPStatusError: Etsy API failure (creator caller can retry; idempotent
            via Listing.etsy_listing_id check on next call).
    """
    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")
    design = session.get(Design, design_id)
    if design is None:
        raise ValueError(f"Design {design_id} not found")
    if design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in listing creation")

    # 2. Idempotency
    existing = _existing_listing(session, template_id, design_id)
    if existing is not None and existing.etsy_listing_id:
        logger.info(
            "Idempotent return: listing %d (etsy=%s) already exists for template=%d design=%d",
            existing.id, existing.etsy_listing_id, template_id, design_id,
        )
        return {
            "listing_id": existing.id,
            "etsy_listing_id": existing.etsy_listing_id,
            "draft_url": _draft_url(existing.etsy_listing_id),
            "composite_urls": [],
            "idempotent": True,
        }

    enabled = _validate_combos(template, enabled_combos)

    # Pre-flight checks against Etsy's hard caps; fail fast before any Etsy call
    issues = listing_pre_check.pre_check_listing(
        title=title,
        tags=tags,
        enabled_combos=enabled,
        composite_size=None,  # composite size checked once first render done
    )
    if issues:
        raise listing_pre_check.PreCheckFailed(issues)

    # 3. Render full image pool (parallel); errors per-image are caught inside
    rendered = composite_service.render_all_for_listing(
        session, template_id, design_id, zone_designs=zone_designs
    )
    # Gallery: successful renders only, sorted by rank, capped at 10
    gallery = sorted(
        [r for r in rendered if r["url"] is not None],
        key=lambda r: r["rank"],
    )[:10]

    # 4. Etsy taxonomy lookup — resolve colors present in gallery
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    taxonomy_id = int(opts.get("etsy_taxonomy_id") or etsy_taxonomy.TAXONOMY_APPAREL_TSHIRT)

    distinct_sizes = sorted({c["size"] for c in enabled})
    distinct_colors = sorted({c["color"] for c in enabled})

    with EtsyApiClient(session) as client:
        size_entries = etsy_taxonomy.resolve_property_inventory_entries(
            client, taxonomy_id, etsy_taxonomy.PROPERTY_SIZE, distinct_sizes,
            preferred_scale_id=etsy_taxonomy.DEFAULT_SIZE_SCALE_ID,
        )
        color_entries = etsy_taxonomy.resolve_property_inventory_entries(
            client, taxonomy_id, etsy_taxonomy.PROPERTY_PRIMARY_COLOR, distinct_colors,
        )
        size_to_entry = dict(zip(distinct_sizes, size_entries))
        color_to_entry = dict(zip(distinct_colors, color_entries))

        # 5. Etsy create draft
        size_price = _size_price_map(template)
        if not size_price:
            raise ValueError(
                f"Template {template_id} variation_options.sizes missing per-size price_cents"
            )
        primary_size_price = next(iter(size_price.values())) / 100.0  # USD float

        shipping_profile_id = (
            opts.get("shipping_profile_id")
            or settings.etsy_default_shipping_profile_id
        )
        if not shipping_profile_id:
            raise ValueError(
                "Etsy requires shipping_profile_id for physical listings — "
                "set template.variation_options.shipping_profile_id or "
                "ETSY_DEFAULT_SHIPPING_PROFILE_ID in .env."
            )
        readiness_state_id = (
            opts.get("readiness_state_id")
            or settings.etsy_default_readiness_state_id
        )
        if not readiness_state_id:
            raise ValueError(
                "Etsy requires readiness_state_id for physical listings — "
                "set template.variation_options.readiness_state_id or "
                "ETSY_DEFAULT_READINESS_STATE_ID in .env."
            )

        # Calculated shipping profiles need package weight + dimensions on the
        # listing. POD t-shirt defaults; template can override per-field.
        dims = {
            "item_weight": float(opts.get("item_weight", 6.0)),
            "item_weight_unit": opts.get("item_weight_unit", "oz"),
            "item_length": float(opts.get("item_length", 12.0)),
            "item_width": float(opts.get("item_width", 10.0)),
            "item_height": float(opts.get("item_height", 1.0)),
            "item_dimensions_unit": opts.get("item_dimensions_unit", "in"),
        }

        draft_resp = client.create_draft_listing(
            shop_id,
            title=title,
            description=description,
            price=primary_size_price,
            quantity=quantity_per_variant,
            taxonomy_id=taxonomy_id,
            tags=tags,
            shipping_profile_id=int(shipping_profile_id),
            readiness_state_id=int(readiness_state_id),
            **dims,
        )
        etsy_listing_id = str(draft_resp.get("listing_id") or draft_resp.get("results", [{}])[0].get("listing_id"))
        if not etsy_listing_id or etsy_listing_id == "None":
            raise ValueError(f"Etsy create_draft_listing returned no listing_id: {draft_resp}")

        # 6. Etsy update inventory
        products = []
        for combo in enabled:
            size, color = combo["size"], combo["color"]
            price = size_price.get(size, int(primary_size_price * 100)) / 100.0
            products.append({
                "sku": f"T{template_id}-D{design_id}-{size}-{color}",
                "property_values": [
                    size_to_entry[size],
                    color_to_entry[color],
                ],
                "offerings": [{
                    "price": price,
                    "quantity": quantity_per_variant,
                    "is_enabled": True,
                    "readiness_state_id": int(readiness_state_id),
                }],
            })
        client.update_listing_inventory(
            etsy_listing_id,
            products,
            price_on_property=[etsy_taxonomy.PROPERTY_SIZE],
            quantity_on_property=[],
            sku_on_property=[
                etsy_taxonomy.PROPERTY_SIZE,
                etsy_taxonomy.PROPERTY_PRIMARY_COLOR,
            ],
        )

        # 7. Sequential image uploads sorted by rank, top-10
        uploaded: list[dict] = []
        for rank, item in enumerate(gallery, start=1):
            try:
                with httpx.Client(timeout=30, follow_redirects=True) as h:
                    img_bytes = h.get(item["url"]).content
                resp = client.upload_listing_image_bytes(
                    shop_id,
                    etsy_listing_id,
                    img_bytes,
                    filename=f"img-{item.get('id') or rank}.png",
                    rank=rank,
                )
                etsy_image_id = int(
                    resp.get("listing_image_id") or resp.get("image_id") or 0
                )
                if etsy_image_id == 0:
                    logger.warning(
                        "upload_listing_image_bytes returned no image id for listing=%s rank=%d",
                        etsy_listing_id, rank,
                    )
                uploaded.append({
                    "rank": rank,
                    "color": item["color"],
                    "url": item["url"],
                    "etsy_image_id": etsy_image_id,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Etsy image upload failed for listing=%s rank=%d: %s",
                    etsy_listing_id, rank, exc,
                )
            time.sleep(IMAGE_UPLOAD_GAP_SEC)

        # 7b. Bind per-color variation hero images (single call; failure non-fatal)
        color_to_etsy_image_id: dict[str, int] = {}
        for u in uploaded:
            if u["color"] and u["etsy_image_id"] and u["color"] not in color_to_etsy_image_id:
                color_to_etsy_image_id[u["color"]] = u["etsy_image_id"]
        variation_images_bound = 0
        if color_to_etsy_image_id:
            try:
                value_to_image_id = {
                    int(color_to_entry[c]["value_ids"][0]): img_id
                    for c, img_id in color_to_etsy_image_id.items()
                    if c in color_to_entry
                }
                if value_to_image_id:
                    client.set_variation_images(
                        shop_id,
                        etsy_listing_id,
                        property_id=etsy_taxonomy.PROPERTY_PRIMARY_COLOR,
                        value_to_image_id=value_to_image_id,
                    )
                    variation_images_bound = len(value_to_image_id)
                    logger.info(
                        "Variation images bound for %d color(s) on listing=%s",
                        variation_images_bound, etsy_listing_id,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Variation images bind failed (listing=%s): %s", etsy_listing_id, exc
                )
        composite_urls_list = uploaded

    # 8. Persist Listing
    listing = Listing(
        etsy_listing_id=etsy_listing_id,
        original_title=title,
        original_desc=description,
        original_tags=json.dumps(tags),
        original_images=json.dumps([c["url"] for c in composite_urls_list]),
        status="created",
        template_id=template_id,
        design_id=design_id,
    )
    session.add(listing)
    session.commit()
    session.refresh(listing)

    return {
        "listing_id": listing.id,
        "etsy_listing_id": etsy_listing_id,
        "draft_url": _draft_url(etsy_listing_id),
        "composite_urls": composite_urls_list,
        "variation_images_bound": variation_images_bound,
        "idempotent": False,
    }


def _draft_url(etsy_listing_id: str) -> str:
    """Build the seller's edit URL for a listing in Shop Manager."""
    return f"https://www.etsy.com/your/shops/me/tools/listings/{etsy_listing_id}"
