"""Composite service — R2-cached template × design compositing with invalidation."""
import hashlib
import json
import logging
from io import BytesIO

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "composites/"


def _safe_color_token(color: str) -> str:
    """Normalize a color name to a filesystem/URL-safe token."""
    return "".join(ch if ch.isalnum() else "-" for ch in color.strip().title())


def _cache_key(template_id: int, design_id: int, color: str | None = None) -> str:
    if color is None:
        return f"{_CACHE_PREFIX}{template_id}-{design_id}.png"
    return f"{_CACHE_PREFIX}{template_id}-{design_id}-{_safe_color_token(color)}.png"


def _multi_zone_cache_key(
    template_id: int,
    design_id: int,
    color: str | None,
    zone_designs: dict[str, int] | None,
) -> str:
    """Legacy cache key (color-based) — kept for backward-compat callers."""
    distinct_dids = set((zone_designs or {}).values())
    is_multi = (
        zone_designs is not None
        and len(zone_designs) > 0
        and (len(distinct_dids) > 1 or design_id not in distinct_dids)
    )
    if not is_multi:
        return _cache_key(template_id, design_id, color)
    compound = "_".join(f"{k}-{v}" for k, v in sorted((zone_designs or {}).items()))
    compound_hash = hashlib.sha1(compound.encode()).hexdigest()[:10]
    suffix = f"-{_safe_color_token(color)}" if color else ""
    return f"{_CACHE_PREFIX}{template_id}-{compound_hash}{suffix}-multi.png"


def _cache_key_v3(
    template_id: int,
    image_id: int | str,
    design_id: int,
    zone_designs: dict[str, int] | None,
) -> str:
    """New cache key using image_id instead of color.

    Single-zone: composites/{tid}-{img_id}-{did}.png
    Multi-zone:  composites/{tid}-{img_id}-{hash10}-multi.png
    """
    distinct_dids = set((zone_designs or {}).values())
    is_multi = (
        zone_designs is not None
        and len(zone_designs) > 0
        and (len(distinct_dids) > 1 or design_id not in distinct_dids)
    )
    if not is_multi:
        return f"{_CACHE_PREFIX}{template_id}-{image_id}-{design_id}.png"
    compound = "_".join(f"{k}-{v}" for k, v in sorted(zone_designs.items()))
    h = hashlib.sha1(compound.encode()).hexdigest()[:10]
    return f"{_CACHE_PREFIX}{template_id}-{image_id}-{h}-multi.png"


def _fetch_bytes(url: str) -> bytes:
    """Fetch URL bytes with browser-ish UA to avoid Cloudflare R2 403."""
    import urllib.request
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (EtsyAuto-Composite/1.0)"}
    )
    with urllib.request.urlopen(req) as resp:  # noqa: S310
        return resp.read()


def _render_with_view(
    session: Session,
    template_id: int,
    design_id: int,
    view: dict,
    zone_designs: dict[str, int] | None,
    cache_key: str,
) -> tuple[str, bool]:
    """Core render pipeline — shared by get_or_create_composite and render_all_for_listing.

    Expects caller to have already resolved ``view`` and computed ``cache_key``.
    Does NOT handle lifestyle_no_fill short-circuit (caller does that before calling here).
    """
    from app.clients.r2_storage_client import R2StorageClient
    from app.models.design import Design
    from app.services import anchor_schema
    from app.services.image_composite import composite_zones

    # Validate default design
    default_design = session.get(Design, design_id)
    if default_design is None:
        raise ValueError(f"Design {design_id} not found")
    if default_design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in composite preview")

    # Parse zones from the image view's anchor
    zones = anchor_schema.parse_anchor(view["anchor_json"])
    if not zones:
        raise ValueError(f"template_image {view['id']!r} has no fill zones")

    # Validate per-zone designs up front (cheap DB lookup, defer network until after cache check)
    zone_design_rows: dict[str, "Design"] = {}
    for zone in zones:
        zone_design_id = (zone_designs or {}).get(zone["name"], design_id)
        d = (
            default_design
            if zone_design_id == design_id
            else session.get(Design, zone_design_id)
        )
        if d is None:
            raise ValueError(f"Design {zone_design_id} (zone {zone['name']!r}) not found")
        if d.source_type == "reference_only":
            raise ValueError(
                f"reference_only design {d.id} cannot be used in zone {zone['name']!r}"
            )
        zone_design_rows[zone["name"]] = d

    r2 = R2StorageClient()
    if r2.object_exists(cache_key):
        url = r2.get_public_url(cache_key)
        logger.info("Composite cache hit: %s", cache_key)
        return url, True

    try:
        base_bytes = _fetch_bytes(view["image_url"])
    except Exception as exc:
        raise ValueError(f"Failed to download template base image: {exc}") from exc

    designs_by_name: dict[str, bytes] = {}
    for name, d in zone_design_rows.items():
        try:
            designs_by_name[name] = _fetch_bytes(d.file_url)
        except Exception as exc:
            raise ValueError(f"Failed to download design image: {exc}") from exc

    output_bytes = composite_zones(base_bytes, zones, designs_by_name)
    url = r2.upload_image(output_bytes, cache_key)
    logger.info(
        "Composite created and cached: %s (%d bytes, %d zone(s))",
        cache_key, len(output_bytes), len(zones),
    )
    return url, False


def _virtual_image_id(view: dict) -> str:
    """Stable pseudo-id for virtual rows (id=None): 'v{rank}'."""
    return f"v{view['rank']}"


def get_or_create_composite(
    session: Session,
    template_id: int,
    design_id: int,
    color: str | None = None,
    zone_designs: dict[str, int] | None = None,
    template_image_id: int | None = None,
) -> tuple[str, bool]:
    """Return composite URL, creating and caching if needed.

    Args:
        session: Active SQLAlchemy session.
        template_id: Template to use as base.
        design_id: Default design (must not be reference_only).
        color: DEPRECATED. Selects per-color base image via legacy path. Preserved for
            backward compat — produces OLD cache key shape (composites/{tid}-{did}-Color.png).
        zone_designs: Optional {zone_name: design_id} overrides per zone.
        template_image_id: NEW preferred input. Routes through image-id-based pipeline with
            new cache key shape (composites/{tid}-{img_id}-{did}.png).

    Returns:
        (composite_url, cached) — cached=True if R2 object already existed.

    Raises:
        ValueError: If template/design not found, any referenced design is reference_only,
            or template image has no zones.
    """
    from app.services import template_image_service

    # --- New path: template_image_id explicitly provided ---
    if template_image_id is not None:
        view = template_image_service.get(session, template_image_id)
        if view is None or view["template_id"] != template_id:
            raise ValueError(
                f"template_image {template_image_id} not in template {template_id}"
            )
        if view["role"] == "lifestyle_no_fill":
            return view["image_url"], True
        img_id = view["id"]  # real row, never None here
        key = _cache_key_v3(template_id, img_id, design_id, zone_designs)
        return _render_with_view(session, template_id, design_id, view, zone_designs, key)

    # --- Back-compat path: color= (or neither) ---
    # Attempt to resolve via template_image_service for new-style templates;
    # fall back to legacy Template fields if not found.
    if color is not None or True:  # always try image service first for new templates
        view = template_image_service.get_view_by_color(session, template_id, color)
        if view is not None:
            if view["role"] == "lifestyle_no_fill":
                return view["image_url"], True
            # Use old cache key shape so existing R2 cache and tests stay valid
            key = _multi_zone_cache_key(template_id, design_id, color, zone_designs)
            return _render_with_view(session, template_id, design_id, view, zone_designs, key)

    # Pure legacy fallback: template has no image pool rows — use Template model directly
    return _legacy_composite(session, template_id, design_id, color, zone_designs)


def _legacy_composite(
    session: Session,
    template_id: int,
    design_id: int,
    color: str | None,
    zone_designs: dict[str, int] | None,
) -> tuple[str, bool]:
    """Original composite pipeline using Template ORM fields directly."""
    from app.clients.r2_storage_client import R2StorageClient
    from app.models.design import Design
    from app.models.template import Template
    from app.services import anchor_schema
    from app.services.image_composite import composite_zones

    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    default_design = session.get(Design, design_id)
    if default_design is None:
        raise ValueError(f"Design {design_id} not found")
    if default_design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in composite preview")

    zones = anchor_schema.parse_anchor(template.composite_anchor_json)
    if not zones:
        zones = [{"name": "main", "kind": "rect", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]

    zone_design_rows: dict[str, "Design"] = {}
    for zone in zones:
        zone_design_id = (zone_designs or {}).get(zone["name"], design_id)
        d = (
            default_design
            if zone_design_id == design_id
            else session.get(Design, zone_design_id)
        )
        if d is None:
            raise ValueError(f"Design {zone_design_id} (zone {zone['name']!r}) not found")
        if d.source_type == "reference_only":
            raise ValueError(
                f"reference_only design {d.id} cannot be used in zone {zone['name']!r}"
            )
        zone_design_rows[zone["name"]] = d

    if color is None:
        base_image_url = template.base_image_url
    else:
        color_norm = color.strip().title()
        try:
            bases = json.loads(template.color_base_images_json or "{}")
        except (json.JSONDecodeError, TypeError):
            bases = {}
        base_image_url = bases.get(color_norm) or template.base_image_url
        if not base_image_url:
            raise ValueError(
                f"Template {template_id} has neither a color base for {color_norm!r} "
                f"nor a default base_image_url"
            )

    key = _multi_zone_cache_key(template_id, design_id, color, zone_designs)
    r2 = R2StorageClient()

    if r2.object_exists(key):
        url = r2.get_public_url(key)
        logger.info("Composite cache hit: %s", key)
        return url, True

    try:
        base_bytes = _fetch_bytes(base_image_url)
    except Exception as exc:
        raise ValueError(f"Failed to download template base image: {exc}") from exc

    designs_by_name: dict[str, bytes] = {}
    for name, d in zone_design_rows.items():
        try:
            designs_by_name[name] = _fetch_bytes(d.file_url)
        except Exception as exc:
            raise ValueError(f"Failed to download design image: {exc}") from exc

    output_bytes = composite_zones(base_bytes, zones, designs_by_name)
    url = r2.upload_image(output_bytes, key)
    logger.info(
        "Composite created and cached: %s (%d bytes, %d zone(s))",
        key, len(output_bytes), len(zones),
    )
    return url, False


def render_all_for_listing(
    session: Session,
    template_id: int,
    design_id: int,
    zone_designs: dict[str, int] | None = None,
) -> list[dict]:
    """Render composites for every template_image in parallel.

    Returns one entry per image ordered by rank (same as list_for_template output):
        {id, template_id, image_url, color, rank, role, is_virtual, url, cached, error}

    Per-image errors are caught and returned in the ``error`` field; never re-raised.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.services import template_image_service

    images = template_image_service.list_for_template(session, template_id)

    def _one(view: dict) -> dict:
        try:
            if view["role"] == "lifestyle_no_fill":
                return {**view, "url": view["image_url"], "cached": True, "error": None}
            img_id = view["id"] if view["id"] is not None else _virtual_image_id(view)
            key = _cache_key_v3(template_id, img_id, design_id, zone_designs)
            url, cached = _render_with_view(
                session, template_id, design_id, view, zone_designs, key
            )
            return {**view, "url": url, "cached": cached, "error": None}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "render_all_for_listing failed tid=%d img_id=%s: %s",
                template_id, view.get("id"), exc,
            )
            return {**view, "url": None, "cached": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(_one, images))


def get_or_create_composites_all_colors(
    session: Session,
    template_id: int,
    design_id: int,
    max_workers: int = 5,
) -> list[dict]:
    """Render composites for every color listed in template.variation_options.colors in parallel.

    Returns list of {color, composite_url, cached, error} per color.

    Raises:
        ValueError: If template/design not found, design is reference_only, or no colors defined.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.models.design import Design
    from app.models.template import Template

    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    design = session.get(Design, design_id)
    if design is None:
        raise ValueError(f"Design {design_id} not found")
    if design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in composite preview")

    try:
        opts = json.loads(template.variation_options_json or "{}")
    except (json.JSONDecodeError, TypeError):
        opts = {}
    colors = [str(c) for c in opts.get("colors", [])]
    if not colors:
        raise ValueError(
            f"Template {template_id} has no colors defined in variation_options"
        )

    def _render_one(color: str) -> dict:
        try:
            url, cached = get_or_create_composite(session, template_id, design_id, color)
            return {"color": color, "composite_url": url, "cached": cached, "error": None}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Per-color composite failed for tid=%d did=%d color=%s: %s",
                template_id, design_id, color, exc,
            )
            return {"color": color, "composite_url": None, "cached": False, "error": str(exc)}

    workers = max(1, min(max_workers, len(colors)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_render_one, colors))
    return results


def invalidate_composites_for_template(template_id: int) -> int:
    """Delete all cached composites for a template from R2.

    Returns number of objects deleted.
    """
    from app.clients.r2_storage_client import R2StorageClient
    r2 = R2StorageClient()
    prefix = f"{_CACHE_PREFIX}{template_id}-"
    keys = r2.list_objects(prefix)
    count = 0
    for key in keys:
        r2.delete_object(key)
        count += 1
    if count:
        logger.info("Invalidated %d composite(s) for template %d", count, template_id)
    return count


def invalidate_composites_for_image(template_image_id: int) -> int:
    """Delete all cached composites for a specific template_image from R2.

    Returns number of objects deleted.
    """
    from app.clients.r2_storage_client import R2StorageClient
    from app.services import template_image_service
    from sqlalchemy.orm import Session as _Session

    # template_image_service.get needs a session — but we only have the id here.
    # We must create one. Use a thin import to avoid circular deps.
    from app.database import SessionLocal
    with SessionLocal() as session:
        view = template_image_service.get(session, template_image_id)

    if view is None:
        logger.warning("invalidate_composites_for_image: id=%d not found", template_image_id)
        return 0

    r2 = R2StorageClient()
    prefix = f"{_CACHE_PREFIX}{view['template_id']}-{template_image_id}-"
    keys = r2.list_objects(prefix)
    count = 0
    for key in keys:
        r2.delete_object(key)
        count += 1
    if count:
        logger.info(
            "Invalidated %d composite(s) for template_image %d (template %d)",
            count, template_image_id, view["template_id"],
        )
    return count


def invalidate_composites_for_design(design_id: int) -> int:
    """Delete all cached composites involving a design from R2.

    Returns number of objects deleted.
    """
    import re
    from app.clients.r2_storage_client import R2StorageClient

    r2 = R2StorageClient()
    # Match both legacy key shapes (color-based) and new image-id-based shapes:
    #   Legacy:  composites/{tid}-{did}.png
    #            composites/{tid}-{did}-{Color}.png
    #            composites/{tid}-{hash}-{Color}-multi.png  (legacy multi-zone)
    #   New:     composites/{tid}-{img_id}-{did}.png
    #            composites/{tid}-{img_id}-{hash10}-multi.png
    legacy_pattern = re.compile(
        rf"^{re.escape(_CACHE_PREFIX)}\d+-{design_id}(-[\w-]+)?\.png$"
    )
    new_pattern = re.compile(
        rf"^{re.escape(_CACHE_PREFIX)}\d+-[\w]+-{design_id}(\.png|-[\w-]+-multi\.png)$"
    )
    all_keys = r2.list_objects(_CACHE_PREFIX)
    keys_to_delete = [
        k for k in all_keys if legacy_pattern.match(k) or new_pattern.match(k)
    ]
    count = 0
    for key in keys_to_delete:
        r2.delete_object(key)
        count += 1
    if count:
        logger.info("Invalidated %d composite(s) for design %d", count, design_id)
    return count
