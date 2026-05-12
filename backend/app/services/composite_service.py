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
    """Cache key for multi-zone composites.

    Single-design (zone_designs empty/None or 1 entry that matches design_id) →
    legacy single-zone key shape so existing R2 cache stays valid.
    Multi-zone with distinct designs → ``composites/{tid}-{hash}-{Color}-multi.png``.
    """
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


def get_or_create_composite(
    session: Session,
    template_id: int,
    design_id: int,
    color: str | None = None,
    zone_designs: dict[str, int] | None = None,
) -> tuple[str, bool]:
    """Return composite URL, creating and caching if needed.

    Args:
        session: Active SQLAlchemy session.
        template_id: Template to use as base.
        design_id: Default design used when a zone has no explicit override.
            Must not be ``source_type='reference_only'``.
        color: Optional color key — selects per-color base image and namespaces
            the cache key.
        zone_designs: Optional ``{zone_name: design_id}`` map. Each zone in the
            template's anchor uses its mapped design when present; zones without
            an entry fall back to ``design_id``.

    Returns:
        (composite_url, cached) — cached=True if R2 object already existed.

    Raises:
        ValueError: If template/design not found, any referenced design is
            reference_only, or template has no zones (anchor empty).
    """
    from app.clients.r2_storage_client import R2StorageClient
    from app.models.design import Design
    from app.models.template import Template
    from app.services import anchor_schema
    from app.services.image_composite import composite_zones

    # Validate template
    template = session.get(Template, template_id)
    if template is None:
        raise ValueError(f"Template {template_id} not found")

    # Validate default design (used when a zone has no override)
    default_design = session.get(Design, design_id)
    if default_design is None:
        raise ValueError(f"Design {design_id} not found")
    if default_design.source_type == "reference_only":
        raise ValueError("reference_only designs cannot be used in composite preview")

    # Resolve zones; legacy v1 templates auto-upcast to a single 'main' rect zone.
    zones = anchor_schema.parse_anchor(template.composite_anchor_json)
    if not zones:
        # Legacy fall-through — treat as full-canvas rect for backwards compat
        zones = [{"name": "main", "kind": "rect", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]

    # Validate per-zone designs *up front* (cheap DB lookup) but defer the
    # network download until after the cache-hit check, so a cache hit costs
    # nothing extra.
    zone_design_rows: dict[str, "Design"] = {}
    for zone in zones:
        zone_design_id = (zone_designs or {}).get(zone["name"], design_id)
        d = (
            default_design
            if zone_design_id == design_id
            else session.get(Design, zone_design_id)
        )
        if d is None:
            raise ValueError(
                f"Design {zone_design_id} (zone {zone['name']!r}) not found"
            )
        if d.source_type == "reference_only":
            raise ValueError(
                f"reference_only design {d.id} cannot be used in zone {zone['name']!r}"
            )
        zone_design_rows[zone["name"]] = d

    # Resolve base image URL: per-color → fallback to template default.
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

    # Cache hit check — return before any network downloads
    if r2.object_exists(key):
        url = r2.get_public_url(key)
        logger.info("Composite cache hit: %s", key)
        return url, True

    # Download template base + per-zone designs.
    # Cloudflare R2 public buckets 403 the default python-urllib User-Agent,
    # so we set a browser-ish UA on every fetch.
    import urllib.request

    def _fetch(url: str) -> bytes:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (EtsyAuto-Composite/1.0)"}
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            return resp.read()

    try:
        base_bytes = _fetch(base_image_url)
    except Exception as exc:
        raise ValueError(f"Failed to download template base image: {exc}") from exc

    designs_by_name: dict[str, bytes] = {}
    for name, d in zone_design_rows.items():
        try:
            designs_by_name[name] = _fetch(d.file_url)
        except Exception as exc:
            raise ValueError(f"Failed to download design image: {exc}") from exc

    # Composite all zones in array order
    output_bytes = composite_zones(base_bytes, zones, designs_by_name)

    # Upload to R2 cache
    url = r2.upload_image(output_bytes, key)
    logger.info(
        "Composite created and cached: %s (%d bytes, %d zone(s))",
        key, len(output_bytes), len(zones),
    )
    return url, False


def get_or_create_composites_all_colors(
    session: Session,
    template_id: int,
    design_id: int,
    max_workers: int = 5,
) -> list[dict]:
    """Render composites for every color listed in template.variation_options.colors in parallel.

    Args:
        session: Active SQLAlchemy session.
        template_id: Template id.
        design_id: Design id (must not be reference_only).
        max_workers: ThreadPoolExecutor cap (default 5).

    Returns:
        List of dicts: `{color, composite_url, cached, error}` per color. `error` is
        a string if that color's render failed (eg missing base image), else None.

    Raises:
        ValueError: If template/design not found, design is reference_only, or
            template has no colors defined.
    """
    from concurrent.futures import ThreadPoolExecutor
    from app.models.template import Template
    from app.models.design import Design

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

    Args:
        template_id: Template whose composites should be invalidated.

    Returns:
        Number of objects deleted.
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


def invalidate_composites_for_design(design_id: int) -> int:
    """Delete all cached composites involving a design from R2.

    Args:
        design_id: Design whose composites should be invalidated.

    Returns:
        Number of objects deleted.
    """
    from app.clients.r2_storage_client import R2StorageClient
    import re
    r2 = R2StorageClient()
    # Match cache keys for this design across both single and per-color variants:
    #   composites/{tid}-{did}.png            (color=None)
    #   composites/{tid}-{did}-{Color}.png    (color set; alphanumeric/dash token)
    pattern = re.compile(rf"^{re.escape(_CACHE_PREFIX)}\d+-{design_id}(-[\w-]+)?\.png$")
    all_keys = r2.list_objects(_CACHE_PREFIX)
    keys_to_delete = [k for k in all_keys if pattern.match(k)]
    count = 0
    for key in keys_to_delete:
        r2.delete_object(key)
        count += 1
    if count:
        logger.info("Invalidated %d composite(s) for design %d", count, design_id)
    return count
