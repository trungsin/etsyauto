"""Listing Creator orchestrator — turns (template + design) into an Etsy draft listing.

Steps (idempotent on Listing.template_id+design_id):
    1. Validate template + design + enabled_combos
    2. Idempotency check: existing Listing for (template_id, design_id) → return as-is
    3. Render composites for all enabled colors (parallel via composite_service)
    4. Resolve Etsy property value IDs (color, size) via taxonomy lookup
    5. Etsy create draft listing
    6. Etsy PUT inventory with full variations matrix
    7. Etsy POST listing images sequentially (200ms gap), primary_color → rank 1
    8. Persist Listing(etsy_listing_id, template_id, design_id, status='created')
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.etsy_api_client import EtsyApiClient
from app.models.design import Design
from app.models.listing import Listing
from app.models.template import Template
from app.services import composite_service, etsy_taxonomy

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
    """Extract {size_name: price_cents} from template.variation_options_json."""
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    out: dict[str, int] = {}
    for s in opts.get("sizes", []):
        if isinstance(s, dict) and "name" in s and "price_cents" in s:
            out[s["name"]] = int(s["price_cents"])
    return out


def _ordered_colors_for_images(template: Template, used_colors: set[str]) -> list[str]:
    """Return colors in image-rank order: primary first, then template-defined order, all from used set."""
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    primary = (opts.get("primary_color") or "").strip().title()
    template_order = [c.strip().title() for c in opts.get("colors", [])]

    ordered: list[str] = []
    if primary and primary in used_colors:
        ordered.append(primary)
    for c in template_order:
        if c in used_colors and c not in ordered:
            ordered.append(c)
    return ordered


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
) -> dict:
    """Create an Etsy draft listing from a template + design.

    Returns:
        {
            "listing_id": int,            # local Listing.id
            "etsy_listing_id": str,
            "draft_url": str,
            "composite_urls": [{"color": str, "url": str}, ...]
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

    # 3. Render composites (parallel)
    used_colors = {c["color"] for c in enabled}
    composites_by_color: dict[str, str] = {}
    for color in used_colors:
        url, _cached = composite_service.get_or_create_composite(
            session, template_id, design_id, color
        )
        composites_by_color[color] = url

    # 4. Etsy taxonomy lookup
    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    taxonomy_id = int(opts.get("etsy_taxonomy_id") or etsy_taxonomy.TAXONOMY_APPAREL_TSHIRT)

    distinct_sizes = sorted({c["size"] for c in enabled})
    distinct_colors = sorted(used_colors)

    with EtsyApiClient(session) as client:
        size_value_ids = etsy_taxonomy.resolve_property_values(
            client, taxonomy_id, etsy_taxonomy.PROPERTY_SIZE, distinct_sizes
        )
        color_value_ids = etsy_taxonomy.resolve_property_values(
            client, taxonomy_id, etsy_taxonomy.PROPERTY_PRIMARY_COLOR, distinct_colors
        )
        size_to_value = dict(zip(distinct_sizes, size_value_ids))
        color_to_value = dict(zip(distinct_colors, color_value_ids))

        # 5. Etsy create draft
        size_price = _size_price_map(template)
        if not size_price:
            raise ValueError(
                f"Template {template_id} variation_options.sizes missing per-size price_cents"
            )
        primary_size_price = next(iter(size_price.values())) / 100.0  # USD float

        draft_resp = client.create_draft_listing(
            shop_id,
            title=title,
            description=description,
            price=primary_size_price,
            quantity=quantity_per_variant,
            taxonomy_id=taxonomy_id,
            tags=tags,
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
                    {
                        "property_id": etsy_taxonomy.PROPERTY_SIZE,
                        "value_ids": [size_to_value[size]],
                        "values": [size],
                    },
                    {
                        "property_id": etsy_taxonomy.PROPERTY_PRIMARY_COLOR,
                        "value_ids": [color_to_value[color]],
                        "values": [color],
                    },
                ],
                "offerings": [{
                    "price": price,
                    "quantity": quantity_per_variant,
                    "is_enabled": True,
                }],
            })
        client.update_listing_inventory(
            etsy_listing_id,
            products,
            price_on_property=[etsy_taxonomy.PROPERTY_SIZE],
            quantity_on_property=[],
            sku_on_property=[],
        )

        # 7. Sequential image uploads (primary first)
        ordered = _ordered_colors_for_images(template, used_colors)
        composite_urls_list: list[dict] = []
        for rank, color in enumerate(ordered, start=1):
            comp_url = composites_by_color.get(color)
            if not comp_url:
                continue
            try:
                with httpx.Client(timeout=30, follow_redirects=True) as h:
                    img_bytes = h.get(comp_url).content
                client.upload_listing_image_bytes(
                    shop_id,
                    etsy_listing_id,
                    img_bytes,
                    filename=f"mockup-{color}.png",
                    rank=rank,
                )
                composite_urls_list.append({"color": color, "url": comp_url, "rank": rank})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Etsy image upload failed for listing=%s color=%s rank=%d: %s",
                    etsy_listing_id, color, rank, exc,
                )
            time.sleep(IMAGE_UPLOAD_GAP_SEC)

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
        "idempotent": False,
    }


def _draft_url(etsy_listing_id: str) -> str:
    """Build the seller's draft-edit URL for a listing."""
    return f"https://www.etsy.com/your/shops/me/listings/draft/{etsy_listing_id}"
