"""Reference service — CRUD for Reference rows with idempotent scrape and cascade cleanup."""
import json
import logging
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clients.gemini_text_client import GeminiTextClient
from app.clients.removebg_client import RemoveBgClient
from app.models.reference import Reference

logger = logging.getLogger(__name__)

_VALID_TAGS = {"style", "color", "layout", "season", "niche"}
_VALID_STATUSES = {"scraped", "enriched", "saved"}
_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def create_or_get_reference(
    session: Session,
    source_url: str,
    source_listing_id: str,
    original_title: str | None,
    original_description: str | None,
    original_images: list[str],
) -> tuple[Reference, bool]:
    """Insert a new Reference or return existing one (idempotent on source_listing_id).

    Args:
        session: Active SQLAlchemy session.
        source_url: Full Etsy listing URL.
        source_listing_id: Unique listing ID string (Etsy listing number).
        original_title: Scraped listing title.
        original_description: Scraped listing description.
        original_images: List of image URLs (max 10).

    Returns:
        (Reference instance, created: bool) — created=False means row already existed.

    Raises:
        ValueError: If more than 10 images provided.
    """
    if len(original_images) > 10:
        raise ValueError(f"Too many images: {len(original_images)} > 10 allowed")

    existing = session.scalars(
        select(Reference).where(Reference.source_listing_id == source_listing_id)
    ).first()
    if existing:
        logger.info("Reference already exists for listing_id=%s id=%d", source_listing_id, existing.id)
        return existing, False

    ref = Reference(
        source_url=source_url,
        source_listing_id=source_listing_id,
        original_title=original_title,
        original_description=original_description,
        original_images_json=json.dumps(original_images),
        status="scraped",
    )
    session.add(ref)
    session.commit()
    session.refresh(ref)
    logger.info("Created reference id=%d listing_id=%s", ref.id, source_listing_id)
    return ref, True


def list_references(
    session: Session,
    tags: str | None = None,
    status: str | None = None,
) -> list[Reference]:
    """Return references ordered by id desc, optionally filtered.

    Args:
        session: Active SQLAlchemy session.
        tags: Comma-separated tag strings to filter by (any match).
        status: Status string to filter by.
    """
    stmt = select(Reference).order_by(Reference.id.desc())
    if status:
        stmt = stmt.where(Reference.status == status)
    rows = list(session.scalars(stmt).all())

    if tags:
        wanted = {t.strip() for t in tags.split(",") if t.strip()}
        rows = [
            r for r in rows
            if _row_has_any_tag(r, wanted)
        ]
    return rows


def _row_has_any_tag(ref: Reference, wanted: set[str]) -> bool:
    """Return True if the reference's tags_json contains any of wanted."""
    if not ref.tags_json:
        return False
    try:
        row_tags = set(json.loads(ref.tags_json))
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(row_tags & wanted)


def get_reference(session: Session, reference_id: int) -> Reference | None:
    """Return a single Reference by id, or None if not found."""
    return session.get(Reference, reference_id)


def update_reference(session: Session, reference_id: int, **fields) -> Reference:
    """Partially update a Reference row.

    Args:
        session: Active SQLAlchemy session.
        reference_id: ID of the reference to update.
        **fields: Allowed: edited_title, notes, tags, kept_image_indices, status,
                  ai_title_variants, notion_page_id, cutout_design_id.

    Returns:
        Updated Reference instance.

    Raises:
        ValueError: If reference not found or invalid field values.
    """
    ref = session.get(Reference, reference_id)
    if ref is None:
        raise ValueError(f"Reference {reference_id} not found")

    if "edited_title" in fields:
        ref.edited_title = fields["edited_title"]
    if "notes" in fields:
        ref.notes = fields["notes"]
    if "tags" in fields:
        tags = fields["tags"] or []
        invalid = set(tags) - _VALID_TAGS
        if invalid:
            raise ValueError(f"Invalid tags: {invalid}. Allowed: {_VALID_TAGS}")
        ref.tags_json = json.dumps(tags)
    if "kept_image_indices" in fields:
        ref.kept_image_indices_json = json.dumps(fields["kept_image_indices"] or [])
    if "status" in fields:
        if fields["status"] not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{fields['status']}'. Allowed: {_VALID_STATUSES}")
        ref.status = fields["status"]
    if "ai_title_variants" in fields:
        ref.ai_title_variants_json = json.dumps(fields["ai_title_variants"] or [])
    if "notion_page_id" in fields:
        ref.notion_page_id = fields["notion_page_id"]
    if "cutout_design_id" in fields:
        ref.cutout_design_id = fields["cutout_design_id"]

    session.commit()
    session.refresh(ref)
    logger.info("Updated reference id=%d fields=%s", reference_id, list(fields.keys()))
    return ref


def generate_title_variants(session: Session, reference_id: int) -> list[dict]:
    """Call Gemini with the reference-idea prompt and persist 3 title variants.

    Args:
        session: Active SQLAlchemy session.
        reference_id: ID of the reference to enrich.

    Returns:
        List of variant dicts saved to ai_title_variants_json.

    Raises:
        ValueError: If reference not found or Gemini yields no valid variants.
        google.genai.errors.APIError: On Gemini API errors (caller maps to HTTP status).
    """
    ref = session.get(Reference, reference_id)
    if ref is None:
        raise ValueError(f"Reference {reference_id} not found")

    tags_list: list[str] = []
    if ref.tags_json:
        try:
            tags_list = json.loads(ref.tags_json)
        except (json.JSONDecodeError, TypeError):
            pass

    listing_data = {
        "original_title": ref.original_title or "",
        "original_description": ref.original_description or "",
        "tags": ", ".join(tags_list),
    }

    client = GeminiTextClient(prompt_path=_PROMPTS_DIR / "title-reference-prompt.md")
    variants = client.generate_title_variants(listing_data)

    ref.ai_title_variants_json = json.dumps(variants)
    ref.status = "enriched"
    session.commit()
    session.refresh(ref)
    logger.info("Saved %d title variants for reference id=%d", len(variants), reference_id)
    return variants


def create_cutout(session: Session, reference_id: int, image_url: str):
    """Download image, strip background via remove.bg, store as Design, link to reference.

    Args:
        session: Active SQLAlchemy session.
        reference_id: ID of the reference.
        image_url: Must be one of reference.original_images.

    Returns:
        New Design instance with source_type='reference_only'.

    Raises:
        ValueError: If reference not found or image_url not in original_images.
        httpx.HTTPStatusError: On download or remove.bg failure.
    """
    ref = session.get(Reference, reference_id)
    if ref is None:
        raise ValueError(f"Reference {reference_id} not found")

    original_images: list[str] = []
    try:
        original_images = json.loads(ref.original_images_json or "[]")
    except (json.JSONDecodeError, TypeError):
        pass

    if image_url not in original_images:
        raise ValueError(
            f"image_url not in original_images for reference {reference_id}"
        )

    # Delete old cutout design if one exists (cascade R2)
    if ref.cutout_design_id is not None:
        try:
            from app.services import design_service
            design_service.delete_design(session, ref.cutout_design_id)
            logger.info("Deleted old cutout design id=%d for reference %d", ref.cutout_design_id, reference_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Old cutout cleanup failed for reference %d: %s", reference_id, exc)
        ref.cutout_design_id = None
        session.commit()

    # Download source image
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        dl_resp = http.get(image_url)
        dl_resp.raise_for_status()
    image_bytes = dl_resp.content

    # Strip background
    rbg_client = RemoveBgClient()
    cutout_bytes = rbg_client.remove_bg(image_bytes)

    # Create design row (validates PNG, uploads to R2)
    from app.services import design_service
    import datetime
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d%H%M%S")
    design = design_service.create_design(
        session=session,
        file_bytes=cutout_bytes,
        name=f"Reference {reference_id} cutout {ts}",
        source_type="reference_only",
    )

    ref.cutout_design_id = design.id
    ref.status = "enriched"
    session.commit()
    session.refresh(ref)
    logger.info("Created cutout design id=%d for reference id=%d", design.id, reference_id)
    return design


def save_to_notion(session: Session, reference_id: int) -> str:
    """Push reference to Notion Idea Bank and return page_id.

    Idempotent: if reference.notion_page_id already set, updates existing page.
    Sets status='saved' and stores page_id after successful create/update.

    Args:
        session: Active SQLAlchemy session.
        reference_id: ID of the reference to push.

    Returns:
        Notion page_id string.

    Raises:
        ValueError: If reference not found.
        RuntimeError: On Notion API errors (caller maps to HTTP status).
    """
    ref = session.get(Reference, reference_id)
    if ref is None:
        raise ValueError(f"Reference {reference_id} not found")

    # Load cutout URL from linked design if present
    cutout_url: str | None = None
    if ref.cutout_design_id is not None:
        try:
            from app.services import design_service
            design = design_service.get_design(session, ref.cutout_design_id)
            if design:
                cutout_url = design.file_url
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load cutout design for reference %d: %s", reference_id, exc)

    from app.clients.notion_client import NotionClient
    client = NotionClient()

    if ref.notion_page_id:
        # Idempotent update — returns same page_id
        client.update_idea_bank_page(ref.notion_page_id, ref, cutout_url)
        page_id = ref.notion_page_id
    else:
        page_id = client.create_idea_bank_page(ref, cutout_url)
        ref.notion_page_id = page_id

    ref.status = "saved"
    session.commit()
    session.refresh(ref)
    logger.info("Saved reference id=%d to Notion page %s", reference_id, page_id)
    return page_id


def delete_reference(session: Session, reference_id: int) -> None:
    """Delete a Reference row with cascade cleanup.

    Cleanup order:
    1. Delete linked cutout Design row (+ R2 file) if exists.
    2. Archive Notion page if linked (best-effort, logs on failure).
    3. Delete Reference row.

    Args:
        session: Active SQLAlchemy session.
        reference_id: ID of the reference to delete.

    Raises:
        ValueError: If reference not found.
    """
    ref = session.get(Reference, reference_id)
    if ref is None:
        raise ValueError(f"Reference {reference_id} not found")

    # Cleanup linked cutout design (cascades R2 delete too)
    if ref.cutout_design_id is not None:
        try:
            from app.services import design_service
            design_service.delete_design(session, ref.cutout_design_id)
            logger.info("Deleted cutout design id=%d for reference id=%d", ref.cutout_design_id, reference_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cutout design cleanup failed for reference %d: %s", reference_id, exc)

    # Archive Notion page (best-effort — do not block delete on Notion failure)
    if ref.notion_page_id:
        try:
            from app.clients.notion_client import NotionClient
            notion = NotionClient()
            notion.archive_page(ref.notion_page_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to archive Notion page %s for reference id=%d: %s",
                ref.notion_page_id, reference_id, exc,
            )

    session.delete(ref)
    session.commit()
    logger.info("Deleted reference id=%d", reference_id)
