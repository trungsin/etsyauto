"""Artwork service — Cloden Design POD pipeline: crop, refine, removebg, upscale.

Each public function corresponds to one pipeline step. All steps update
Artwork.status in DB so the frontend can poll progress. Real-ESRGAN upscale
runs as a FastAPI BackgroundTask (CPU, ~2-4 min for 4x scale).
"""
import logging
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.clients.removebg_client import RemoveBgClient
from app.config import settings
from app.database import SessionLocal
from app.models.artwork import Artwork
from app.services.image_service import download_image, save_to_static, upload_to_r2

logger = logging.getLogger(__name__)

# Cap input dimension before upscaling to avoid CPU OOM on very large crops
_UPSCALE_MAX_INPUT_PX = 1024


def _load_image_bytes(url: str) -> bytes:
    """Load image bytes from a URL or a local /static/ path.

    When R2 is not configured, _save_and_upload returns a relative path like
    '/static/abc.png'. httpx cannot handle relative URLs, so we read from disk
    directly in that case.
    """
    if url.startswith("/static/"):
        filename = Path(url).name
        path = Path(settings.static_dir) / filename
        return path.read_bytes()
    return download_image(url)


def crop_image(image_bytes: bytes, fx: float, fy: float, fw: float, fh: float) -> bytes:
    """Crop image using fractional coordinates in [0, 1]. Returns PNG bytes.

    Args:
        image_bytes: Source image bytes (any PIL-supported format).
        fx, fy: Top-left corner of crop as fraction of image width/height.
        fw, fh: Crop width/height as fraction of image width/height.

    Raises:
        ValueError: If crop region is degenerate (< 1px in any dimension).
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size
    left = int(fx * w)
    top = int(fy * h)
    right = min(w, int((fx + fw) * w))
    bottom = min(h, int((fy + fh) * h))

    if right <= left or bottom <= top:
        raise ValueError(f"Degenerate crop region: ({left},{top},{right},{bottom})")

    cropped = img.crop((left, top, right, bottom))
    buf = BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def _save_and_upload(image_bytes: bytes, ext: str = "png") -> str:
    """Save bytes to static dir, upload to R2 if configured. Returns accessible URL."""
    path = save_to_static(image_bytes, ext=ext)
    r2_url = upload_to_r2(path)
    if r2_url:
        return r2_url
    # Fallback: local static URL (accessible via FastAPI StaticFiles mount)
    return f"/static/{Path(path).name}"


def process_upload_and_crop(
    db: Session,
    file_bytes: bytes,
    name: str,
    fx: float,
    fy: float,
    fw: float,
    fh: float,
) -> Artwork:
    """Upload original + crop to fraction coords. Creates Artwork row with status=cropped.

    Args:
        db: Active SQLAlchemy session.
        file_bytes: Raw image bytes from the upload.
        name: Display name for this artwork.
        fx, fy, fw, fh: Crop region as fractions [0, 1].

    Returns:
        Persisted Artwork with status="cropped".

    Raises:
        ValueError: On degenerate crop or oversized input.
    """
    if len(file_bytes) > 20 * 1024 * 1024:
        raise ValueError("Upload exceeds 20 MB limit")

    original_url = _save_and_upload(file_bytes)
    cropped_bytes = crop_image(file_bytes, fx, fy, fw, fh)
    cropped_url = _save_and_upload(cropped_bytes)

    artwork = Artwork(
        name=name,
        status="cropped",
        original_url=original_url,
        cropped_url=cropped_url,
    )
    db.add(artwork)
    db.commit()
    db.refresh(artwork)
    logger.info("Created artwork id=%d name=%s status=cropped", artwork.id, artwork.name)
    return artwork


def refine_artwork(db: Session, artwork_id: int) -> Artwork:
    """Call GPT-Image-1 to clean/smooth the cropped image. Transitions cropped→refined.

    Raises:
        ValueError: If artwork not found, wrong status, or OpenAI call fails.
    """
    from app.clients.openai_imagen_client import OpenaiImagenClient

    artwork = db.get(Artwork, artwork_id)
    if not artwork:
        raise ValueError(f"Artwork {artwork_id} not found")
    if artwork.status != "cropped":
        raise ValueError(f"Artwork {artwork_id} has status '{artwork.status}', expected 'cropped'")

    logger.info("Artwork %d: calling GPT-Image-1 refine", artwork_id)
    cropped_bytes = _load_image_bytes(artwork.cropped_url)
    client = OpenaiImagenClient()
    refined_bytes = client.edit_image(cropped_bytes)

    refined_url = _save_and_upload(refined_bytes)
    artwork.refined_url = refined_url
    artwork.status = "refined"
    db.commit()
    db.refresh(artwork)
    logger.info("Artwork %d: refined, url=%s", artwork_id, refined_url)
    return artwork


def removebg_artwork(db: Session, artwork_id: int) -> Artwork:
    """Remove background via remove.bg. Transitions refined→removebg_done.

    Raises:
        ValueError: If artwork not found, wrong status, or remove.bg call fails.
    """
    artwork = db.get(Artwork, artwork_id)
    if not artwork:
        raise ValueError(f"Artwork {artwork_id} not found")
    if artwork.status != "refined":
        raise ValueError(f"Artwork {artwork_id} has status '{artwork.status}', expected 'refined'")

    logger.info("Artwork %d: calling remove.bg", artwork_id)
    refined_bytes = _load_image_bytes(artwork.refined_url)
    removebg_bytes = RemoveBgClient().remove_bg(refined_bytes)

    removebg_url = _save_and_upload(removebg_bytes)
    artwork.removebg_url = removebg_url
    artwork.status = "removebg_done"
    db.commit()
    db.refresh(artwork)
    logger.info("Artwork %d: removebg done, url=%s", artwork_id, removebg_url)
    return artwork


def run_upscale_job(artwork_id: int) -> None:
    """BackgroundTask: Real-ESRGAN 4x upscale. Transitions removebg_done→upscaling→done|failed.

    Opens its own DB session (request session has already closed by the time
    this runs as a BackgroundTask). Requires realesrgan + basicsr + torch installed.
    """
    db = SessionLocal()
    artwork = None
    try:
        artwork = db.get(Artwork, artwork_id)
        if not artwork:
            logger.warning("run_upscale_job: artwork %d not found", artwork_id)
            return
        # Guard: route pre-sets "upscaling" — if status changed, another job ran
        if artwork.status not in ("upscaling", "removebg_done"):
            logger.warning("run_upscale_job: artwork %d has unexpected status '%s', skipping", artwork_id, artwork.status)
            return

        input_bytes = _load_image_bytes(artwork.removebg_url)
        output_bytes = _upscale_realesrgan(input_bytes)

        final_url = _save_and_upload(output_bytes)
        artwork.final_url = final_url
        artwork.status = "done"
        artwork.error_message = None
        db.commit()
        logger.info("Artwork %d: upscale done, url=%s", artwork_id, final_url)

    except Exception as exc:  # noqa: BLE001 — background job must not crash the server
        logger.exception("Upscale job failed for artwork %d", artwork_id)
        if artwork is not None:
            artwork.status = "failed"
            artwork.error_message = str(exc)
            db.commit()
    finally:
        db.close()


def _upscale_realesrgan(image_bytes: bytes) -> bytes:
    """Run Real-ESRGAN x4plus_anime_6B (CPU). Caps input at 1024px to avoid OOM.

    Raises:
        ImportError: Propagated if realesrgan/basicsr/torch are not installed.
        ValueError: On any upscaling failure.
    """
    try:
        import numpy as np
        import torch
        from basicsr.archs.rrdbnet_arch import RRDBNet
        from realesrgan import RealESRGANer
    except ImportError as exc:
        raise ImportError(
            "Real-ESRGAN is not installed. Run:\n"
            "  uv pip install realesrgan basicsr torch "
            "--index-url https://download.pytorch.org/whl/cpu\n"
            f"(original error: {exc})"
        ) from exc

    # Cap input to avoid OOM on CPU
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    if max(img.width, img.height) > _UPSCALE_MAX_INPUT_PX:
        ratio = _UPSCALE_MAX_INPUT_PX / max(img.width, img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)
        logger.info("Capped upscale input to %dx%d", img.width, img.height)

    # RealESRGAN_x4plus_anime_6B — best for flat design/digital art, ~17MB model
    model = RRDBNet(
        num_in_ch=3, num_out_ch=3, num_feat=64,
        num_block=6, num_grow_ch=32, scale=4,
    )
    model_url = (
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth"
    )

    upsampler = RealESRGANer(
        scale=4,
        model_path=model_url,  # auto-downloads to ~/.cache/realesrgan/ on first run
        model=model,
        device=torch.device("cpu"),
        half=False,            # FP32 required on CPU (FP16 is for CUDA only)
    )

    # RealESRGAN works on RGB; strip alpha, upscale, re-apply alpha
    rgb = img.convert("RGB")
    alpha = img.getchannel("A")

    rgb_np = np.array(rgb)
    output_np, _ = upsampler.enhance(rgb_np, outscale=4)
    output_rgb = Image.fromarray(output_np)

    # Upscale alpha channel separately (simple bicubic — sufficient for masks)
    output_alpha = alpha.resize(
        (output_rgb.width, output_rgb.height), Image.LANCZOS
    )
    output_rgb.putalpha(output_alpha)

    buf = BytesIO()
    output_rgb.save(buf, format="PNG")
    return buf.getvalue()
