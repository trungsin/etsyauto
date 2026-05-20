"""Listing Creator orchestrator — 2-phase pipeline (v0.10).

Phase A (`save_draft`): validate template/design/combos → render full image pool
→ upsert Listing row with status='new' and serialized local payload. NO Etsy
API call. Caller (wizard or admin route) can preview composites + edit local
fields before committing to Etsy.

Phase B (`upload_to_etsy`): CAS-lock new/failed → uploading → call Etsy
(create_draft + inventory PUT + image upload + variation hero) → set status
to 'created' (success) or 'failed' (error). Idempotent on rows that already
have etsy_listing_id.

`create_from_template` retained as a back-compat shim that runs A then B.
"""
from __future__ import annotations

import json
import logging
import time

import httpx
from sqlalchemy import select, update
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


class ConflictError(Exception):
    """Raised when CAS lock on Listing.status fails (e.g. concurrent upload click)."""


# ---------------------------------------------------------------------------
# Internal helpers (shared by save_draft and upload_to_etsy)
# ---------------------------------------------------------------------------


def _existing_listing(
    session: Session, template_id: int, design_id: int
) -> Listing | None:
    """Return a Listing row already created from this (template, design), or None.

    Excludes soft-deleted rows so a deleted listing doesn't block re-creation.
    """
    stmt = select(Listing).where(
        Listing.template_id == template_id,
        Listing.design_id == design_id,
        Listing.deleted_at.is_(None),
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
    fallback = int(template.default_price_cents or 0)
    out: dict[str, int] = {}
    for s in opts.get("sizes", []):
        if isinstance(s, dict) and "name" in s:
            out[str(s["name"])] = int(s.get("price_cents", fallback))
        elif isinstance(s, str) and fallback > 0:
            out[s] = fallback
    return out


def _draft_url(etsy_listing_id: str) -> str:
    """Build the seller's edit URL for a listing in Shop Manager."""
    return f"https://www.etsy.com/your/shops/me/tools/listings/{etsy_listing_id}"


# ---------------------------------------------------------------------------
# Phase A: save_draft — render composites + persist local row, no Etsy call
# ---------------------------------------------------------------------------


def save_draft(
    session: Session,
    *,
    template_id: int,
    design_id: int,
    title: str,
    description: str,
    tags: list[str],
    enabled_combos: list[dict],
    zone_designs: dict[str, int] | None = None,
) -> dict:
    """Render composites + persist a Listing row with status='new'.

    Upserts on (template_id, design_id): existing draft → updates local_payload
    and re-renders; existing live listing (has etsy_listing_id) → returns
    idempotent result without modification.

    Returns:
        {
            "listing_id": int,
            "status": str,                # "new" | "created" (idempotent)
            "composite_urls": list[dict], # successful renders with rank, color, url
            "idempotent": bool,           # True if existing live listing returned
        }

    Raises:
        ValueError: template/design not found, invalid combos, pre-check failed.
    """
    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")
    design = session.get(Design, design_id)
    if design is None:
        raise ValueError(f"Design {design_id} not found")
    if design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in listing creation")

    # Idempotency: existing live listing → return as-is, do not modify
    existing = _existing_listing(session, template_id, design_id)
    if existing is not None and existing.etsy_listing_id:
        logger.info(
            "save_draft idempotent: listing %d already live on Etsy (id=%s)",
            existing.id, existing.etsy_listing_id,
        )
        return {
            "listing_id": existing.id,
            "status": existing.status,
            "composite_urls": [],
            "idempotent": True,
        }

    enabled = _validate_combos(template, enabled_combos)

    # Pre-flight — fail fast before render
    issues = listing_pre_check.pre_check_listing(
        title=title,
        tags=tags,
        enabled_combos=enabled,
        composite_size=None,
    )
    if issues:
        raise listing_pre_check.PreCheckFailed(issues)

    # Render full image pool (parallel; per-image errors caught inside)
    rendered = composite_service.render_all_for_listing(
        session, template_id, design_id, zone_designs=zone_designs
    )
    gallery = sorted(
        [r for r in rendered if r["url"] is not None],
        key=lambda r: r["rank"],
    )[:10]

    payload = {
        "title": title,
        "description": description,
        "tags": list(tags),
        "enabled_combos": enabled,
        "zone_designs": zone_designs or {},
        # Snapshot of the rendered gallery so upload_to_etsy can skip a second
        # render pass. Keys mirror what _do_etsy_upload needs to push images.
        "gallery_snapshot": [
            {
                "rank": g["rank"],
                "color": g.get("color"),
                "url": g["url"],
                "id": g.get("id"),
            }
            for g in gallery
        ],
    }

    # Upsert: update existing draft or insert new
    if existing is not None:
        existing.original_title = title
        existing.original_desc = description
        existing.original_tags = json.dumps(tags)
        existing.original_images = json.dumps([g["url"] for g in gallery])
        existing.local_payload_json = json.dumps(payload)
        existing.status = "new"
        existing.last_push_error = None
        session.commit()
        session.refresh(existing)
        listing = existing
        logger.info("save_draft updated existing draft listing %d", listing.id)
    else:
        # DB-level UNIQUE(template_id, design_id) doesn't honor soft-delete.
        # If a soft-deleted row exists with same combo, resurrect it instead of
        # inserting (which would hit IntegrityError).
        deleted_row = session.scalars(
            select(Listing).where(
                Listing.template_id == template_id,
                Listing.design_id == design_id,
                Listing.deleted_at.is_not(None),
            )
        ).first()
        if deleted_row is not None:
            deleted_row.deleted_at = None
            deleted_row.etsy_listing_id = None
            deleted_row.pushed_at = None
            deleted_row.push_attempts = 0
            deleted_row.original_title = title
            deleted_row.original_desc = description
            deleted_row.original_tags = json.dumps(tags)
            deleted_row.original_images = json.dumps([g["url"] for g in gallery])
            deleted_row.local_payload_json = json.dumps(payload)
            deleted_row.status = "new"
            deleted_row.last_push_error = None
            session.commit()
            session.refresh(deleted_row)
            listing = deleted_row
            logger.info("save_draft resurrected soft-deleted listing %d", listing.id)
        else:
            listing = Listing(
                etsy_listing_id=None,
                original_title=title,
                original_desc=description,
                original_tags=json.dumps(tags),
                original_images=json.dumps([g["url"] for g in gallery]),
                status="new",
                template_id=template_id,
                design_id=design_id,
                local_payload_json=json.dumps(payload),
            )
            session.add(listing)
            session.commit()
            session.refresh(listing)
            logger.info("save_draft created new draft listing %d", listing.id)

    return {
        "listing_id": listing.id,
        "status": listing.status,
        "composite_urls": [
            {"rank": g["rank"], "color": g.get("color"), "url": g["url"]}
            for g in gallery
        ],
        "idempotent": False,
    }


# ---------------------------------------------------------------------------
# Phase B: upload_to_etsy — CAS lock + Etsy create + inventory + images
# ---------------------------------------------------------------------------


def upload_to_etsy(
    session: Session,
    listing_id: int,
    shop_id: str | int,
    *,
    quantity_per_variant: int = 100,
) -> dict:
    """Push a local-draft listing to Etsy. Idempotent if already uploaded.

    Acquires a CAS lock on Listing.status: only proceeds if status is in
    ('new', 'failed'). Sets status to 'uploading' under the lock, then to
    'created' on success or 'failed' on error.

    Returns:
        Same shape as old create_from_template.

    Raises:
        ValueError: listing missing required fields.
        ConflictError: CAS lock failed (concurrent upload, or wrong state).
        httpx.HTTPStatusError: Etsy API failure (status set to 'failed').
    """
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise ValueError(f"Listing {listing_id} not found")
    if listing.deleted_at is not None:
        raise ValueError(f"Listing {listing_id} is deleted")

    # Idempotency: already uploaded → return as-is
    if listing.etsy_listing_id:
        logger.info(
            "upload_to_etsy idempotent: listing %d already on Etsy (id=%s)",
            listing.id, listing.etsy_listing_id,
        )
        return {
            "listing_id": listing.id,
            "etsy_listing_id": listing.etsy_listing_id,
            "draft_url": _draft_url(listing.etsy_listing_id),
            "composite_urls": [],
            "idempotent": True,
        }

    # CAS lock: status new|failed → uploading. Atomic per-row UPDATE.
    cas = session.execute(
        update(Listing)
        .where(Listing.id == listing_id, Listing.status.in_(("new", "failed")))
        .values(status="uploading")
    )
    session.commit()
    if cas.rowcount == 0:
        raise ConflictError(
            f"Listing {listing_id} not in uploadable state (need 'new' or 'failed', "
            f"got '{listing.status}'). Possibly another upload in progress."
        )
    session.refresh(listing)

    try:
        result = _do_etsy_upload(session, listing, shop_id, quantity_per_variant)
        # Success: status=created, etsy_listing_id set in _do_etsy_upload
        return result
    except Exception as exc:
        # Rollback to failed; preserve error for UI
        session.rollback()
        listing = session.get(Listing, listing_id)  # re-load (rollback wiped state)
        if listing is not None:
            listing.status = "failed"
            listing.last_push_error = f"{type(exc).__name__}: {exc}"
            listing.push_attempts = (listing.push_attempts or 0) + 1
            session.commit()
        logger.warning("upload_to_etsy failed for listing=%d: %s", listing_id, exc)
        raise


def _do_etsy_upload(
    session: Session,
    listing: Listing,
    shop_id: str | int,
    quantity_per_variant: int,
) -> dict:
    """Inner Etsy upload pipeline. Caller wraps in try/except for status rollback."""
    payload = json.loads(listing.local_payload_json or "{}")
    title = payload.get("title") or listing.original_title
    description = payload.get("description") or listing.original_desc or ""
    tags = payload.get("tags") or json.loads(listing.original_tags or "[]")
    enabled = payload.get("enabled_combos") or []
    zone_designs = payload.get("zone_designs") or None

    template = session.get(Template, listing.template_id)
    design_id = listing.design_id
    if template is None or design_id is None:
        raise ValueError("Listing missing template/design refs")

    # Prefer gallery snapshot from save_draft to avoid a redundant render pass.
    # Fall back to re-render if snapshot missing (e.g. legacy draft, or images
    # changed since save_draft via /rerender admin action).
    snapshot = payload.get("gallery_snapshot") or []
    if snapshot:
        gallery = sorted(snapshot, key=lambda r: r["rank"])[:10]
    else:
        rendered = composite_service.render_all_for_listing(
            session, listing.template_id, design_id, zone_designs=zone_designs
        )
        gallery = sorted(
            [r for r in rendered if r["url"] is not None],
            key=lambda r: r["rank"],
        )[:10]

    # Etsy taxonomy + create + inventory + image upload + variation hero
    opts = json.loads(template.variation_options_json or "{}")
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

        size_price = _size_price_map(template)
        if not size_price:
            raise ValueError(
                f"Template {template.id} variation_options.sizes missing per-size price_cents"
            )
        primary_size_price = next(iter(size_price.values())) / 100.0

        shipping_profile_id = (
            opts.get("shipping_profile_id") or settings.etsy_default_shipping_profile_id
        )
        if not shipping_profile_id:
            raise ValueError(
                "Etsy requires shipping_profile_id for physical listings — "
                "set template.variation_options.shipping_profile_id or "
                "ETSY_DEFAULT_SHIPPING_PROFILE_ID in .env."
            )
        readiness_state_id = (
            opts.get("readiness_state_id") or settings.etsy_default_readiness_state_id
        )
        if not readiness_state_id:
            raise ValueError(
                "Etsy requires readiness_state_id for physical listings — "
                "set template.variation_options.readiness_state_id or "
                "ETSY_DEFAULT_READINESS_STATE_ID in .env."
            )

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
        etsy_listing_id = str(
            draft_resp.get("listing_id")
            or draft_resp.get("results", [{}])[0].get("listing_id")
        )
        if not etsy_listing_id or etsy_listing_id == "None":
            raise ValueError(f"Etsy create_draft_listing returned no listing_id: {draft_resp}")

        # Inventory PUT
        products = []
        for combo in enabled:
            size, color = combo["size"], combo["color"]
            price = size_price.get(size, int(primary_size_price * 100)) / 100.0
            products.append({
                "sku": f"T{template.id}-D{design_id}-{size}-{color}",
                "property_values": [size_to_entry[size], color_to_entry[color]],
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

        # Image upload — top-10 by rank
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
                uploaded.append({
                    "rank": rank,
                    "color": item.get("color"),
                    "url": item["url"],
                    "etsy_image_id": etsy_image_id,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Etsy image upload failed for listing=%s rank=%d: %s",
                    etsy_listing_id, rank, exc,
                )
            time.sleep(IMAGE_UPLOAD_GAP_SEC)

        # Variation hero binding (non-fatal)
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Variation images bind failed (listing=%s): %s",
                    etsy_listing_id, exc,
                )

    # Persist success state
    listing.etsy_listing_id = etsy_listing_id
    listing.status = "created"
    listing.last_push_error = None
    from datetime import datetime, timezone
    listing.pushed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    listing.original_images = json.dumps([u["url"] for u in uploaded])
    session.commit()
    session.refresh(listing)

    return {
        "listing_id": listing.id,
        "etsy_listing_id": etsy_listing_id,
        "draft_url": _draft_url(etsy_listing_id),
        "composite_urls": uploaded,
        "variation_images_bound": variation_images_bound,
        "idempotent": False,
    }


# ---------------------------------------------------------------------------
# Back-compat shim — kept for any callers not yet migrated to 2-phase flow
# ---------------------------------------------------------------------------


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
    """Back-compat: save_draft + upload_to_etsy in one call (old single-shot path).

    Prefer the 2-phase API: call save_draft() first, then upload_to_etsy()
    when the user reviews and approves. Retained for callers that haven't
    migrated yet (legacy tests, extension, etc.).
    """
    draft = save_draft(
        session,
        template_id=template_id,
        design_id=design_id,
        title=title,
        description=description,
        tags=tags,
        enabled_combos=enabled_combos,
        zone_designs=zone_designs,
    )
    if draft.get("idempotent"):
        # Already live on Etsy — return the existing live listing info
        listing = session.get(Listing, draft["listing_id"])
        return {
            "listing_id": listing.id,
            "etsy_listing_id": listing.etsy_listing_id,
            "draft_url": _draft_url(listing.etsy_listing_id),
            "composite_urls": [],
            "idempotent": True,
        }
    try:
        return upload_to_etsy(
            session,
            draft["listing_id"],
            shop_id,
            quantity_per_variant=quantity_per_variant,
        )
    except Exception:
        # Back-compat: callers of this shim expect atomic semantics (failure
        # leaves no orphan row). New 2-phase API keeps the failed draft so the
        # admin UI can show it and retry. The 2-phase callers (admin routes,
        # wizard) should call save_draft + upload_to_etsy directly.
        listing = session.get(Listing, draft["listing_id"])
        if listing is not None and not listing.etsy_listing_id:
            session.delete(listing)
            session.commit()
        raise
