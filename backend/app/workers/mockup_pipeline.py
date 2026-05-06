"""Mockup pipeline worker — APScheduler job: remove.bg + Gemini scene replacement."""
import json
import logging
from pathlib import Path

from app.clients.gemini_imagen_client import GeminiImagenClient
from app.clients.removebg_client import RemoveBgClient
from app.database import SessionLocal
from app.services import listing_service
from app.services.image_service import download_image, resize_if_needed, save_to_static, upload_to_r2

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent.parent / "prompts" / "scene_prompts.json"
_GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"


def _load_scene_prompts(category: str = "default") -> list[str]:
    """Load scene prompts for the given category, falling back to 'default'."""
    data = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))
    return data.get(category) or data["default"]


def run_mockup_pipeline_job() -> None:
    """Poll listings with status='mockup-pending', run BG removal + scene generation.

    Each listing is claimed atomically. Errors per listing are caught so one
    failure does not abort the whole batch.
    """
    session = SessionLocal()
    try:
        pending = listing_service.get_pending_for_mockup(session)
        if not pending:
            return

        logger.info("Mockup pipeline: found %d pending listing(s)", len(pending))

        removebg = RemoveBgClient()
        gemini = GeminiImagenClient()
        scene_prompts = _load_scene_prompts("default")

        for listing in pending:
            _process_listing(session, removebg, gemini, scene_prompts, listing)
    finally:
        session.close()


def _process_listing(session, removebg: RemoveBgClient, gemini: GeminiImagenClient,
                     scene_prompts: list[str], listing) -> None:
    """Claim and process one listing; log errors without re-raising."""
    listing_id = listing.id

    claimed = listing_service.mark_mockup_processing(session, listing_id)
    if not claimed:
        logger.info("Listing %d already claimed by another worker — skipping", listing_id)
        return

    try:
        # Parse image URLs — use first URL only (MVP)
        images_raw = listing.original_images or "[]"
        try:
            image_urls = json.loads(images_raw)
        except (json.JSONDecodeError, TypeError):
            image_urls = []

        if not image_urls:
            raise ValueError("Listing has no original_images to process")

        primary_url = image_urls[0]
        logger.info("Listing %d: downloading primary image %s", listing_id, primary_url)

        # Step 1: download + remove background
        raw_bytes = download_image(primary_url)
        cutout_bytes = removebg.remove_bg(raw_bytes)
        cutout_path = save_to_static(cutout_bytes, ext="png")

        # Step 2: generate 3 scene variants
        variants: list[dict] = []
        for scene_prompt in scene_prompts:
            logger.info("Listing %d: generating scene %r", listing_id, scene_prompt[:50])
            scene_bytes = gemini.generate_scene(cutout_bytes, scene_prompt)
            scene_bytes = resize_if_needed(scene_bytes, max_mb=4)
            final_path = save_to_static(scene_bytes, ext="png")

            # Upload to R2 for public URL access by Notion — non-fatal if R2 not configured
            final_url = upload_to_r2(final_path)
            if final_url:
                logger.info("Listing %d: uploaded scene to R2: %s", listing_id, final_url)
            else:
                logger.warning("Listing %d: R2 upload skipped — image only at %s", listing_id, final_path)

            variants.append({
                "source_image_url": primary_url,
                "removed_bg_path": cutout_path,
                "final_image_path": final_path,
                "final_image_url": final_url,
                "scene_prompt": scene_prompt,
                "model": _GEMINI_MODEL,
            })

        listing_service.save_mockup_variants(session, listing_id, variants)
        logger.info("Listing %d: saved %d mockup variants", listing_id, len(variants))

        # Promote to 'review' only when both title + mockup variants are ready
        listing_service.try_promote_to_review(session, listing_id)

    except Exception as exc:  # noqa: BLE001 — per-listing isolation, must not crash job
        logger.exception("Mockup pipeline failed for listing %d: %s", listing_id, exc)
        listing_service.mark_mockup_failed(session, listing_id, str(exc))
