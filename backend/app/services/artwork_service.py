"""Artwork service — Cloden Design POD pipeline: crop, refine, removebg, upscale, canvas.

Each public function corresponds to one pipeline step. All steps update
Artwork.status in DB. Upscale runs via upscayl-bin CLI as a BackgroundTask.
"""
import logging
import shutil
import subprocess
import tempfile
import time
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

# Cap input before upscaling — SwiftShader CPU scales O(pixels); 512px → ~80s, 1024px → ~10min
_UPSCALE_MAX_INPUT_PX = 512

_REFINE_PROMPT_LIGHT = (
    "This is a design artwork cropped from a product mockup image. "
    "Your task: faithfully reconstruct and enhance this design as a clean, high-quality graphic. "
    "1. Remove all mockup context: fabric texture, product shape, wrinkles, shadows, and background noise. "
    "2. Upscale and sharpen the design — increase perceived resolution, make lines crisp, text legible, and details well-defined. "
    "3. Enhance color vibrancy and contrast while preserving the original color scheme exactly. "
    "4. Reconstruct any design elements that appear blurry or degraded by the mockup. "
    "5. Place the final design centered on a perfectly clean white background. "
    "The output must be a faithful, high-quality reproduction of the original design — do NOT alter shapes, layout, or creative intent."
)

_REFINE_PROMPT_DARK = (
    "This is a design artwork cropped from a product mockup image. "
    "Your task: faithfully reconstruct and enhance this design as a clean, high-quality graphic. "
    "1. Remove all mockup context: fabric texture, product shape, wrinkles, shadows, and background noise. "
    "2. Upscale and sharpen the design — increase perceived resolution, make lines crisp, text legible, and details well-defined. "
    "3. Reconstruct any design elements that appear blurry or degraded by the mockup. "
    "4. Place the final design on a perfectly solid black background. "
    "5. Adjust the design colors to be vivid and clearly visible on the dark background: "
    "   lighten or brighten any dark design elements, ensure strong contrast against black. "
    "The output must be a faithful, high-quality reproduction of the original design — do NOT alter shapes, layout, or creative intent."
)


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


def _call_refine_providers(artwork_id: int, cropped_bytes: bytes, prompt: str) -> bytes:
    """Run provider chain (ChatGPT OAuth → Gemini → OpenAI) and return refined image bytes.

    Raises:
        ValueError: If all configured providers fail or none are configured.
    """
    refined_bytes: bytes | None = None
    provider_errors: list[str] = []

    # 1. ChatGPT OAuth (gpt-image-2) — uses Plus subscription, no billing
    from app.clients.chatgpt_oauth_image_client import (
        ChatGPTOAuthImageClient,
        has_session as chatgpt_has_session,
    )
    if chatgpt_has_session():
        for attempt in range(2):
            try:
                logger.info("Artwork %d: ChatGPT OAuth gpt-image-2 refine (attempt=%d)", artwork_id, attempt + 1)
                refined_bytes = ChatGPTOAuthImageClient().edit_for_artwork(cropped_bytes, prompt=prompt)
                break
            except Exception as exc:
                err = str(exc)
                is_rate_limit = "rate limit" in err.lower() or "rate_limit" in err.lower()
                if is_rate_limit and attempt == 0:
                    logger.warning("Artwork %d: ChatGPT rate-limited — retrying in 5s", artwork_id)
                    time.sleep(5)
                    continue
                provider_errors.append(f"ChatGPT OAuth: {err[:200]}")
                logger.warning("Artwork %d: ChatGPT OAuth refine failed: %s", artwork_id, err)
                break

    # 2. Gemini image editing (with key rotation + model fallback in client)
    if refined_bytes is None and (settings.gemini_api_key or settings.gemini_api_keys):
        try:
            from app.clients.gemini_imagen_client import GeminiImagenClient
            logger.info("Artwork %d: Gemini image edit refine", artwork_id)
            refined_bytes = GeminiImagenClient().edit_for_artwork(cropped_bytes, prompt=prompt)
        except Exception as exc:
            err = str(exc)
            provider_errors.append(f"Gemini: {err[:200]}")
            logger.warning("Artwork %d: Gemini refine failed: %s", artwork_id, err)

    # 3. OpenAI GPT-Image-1 API (requires paid credits)
    if refined_bytes is None and settings.openai_api_key:
        try:
            from app.clients.openai_imagen_client import OpenaiImagenClient
            logger.info("Artwork %d: GPT-Image-1 API refine fallback", artwork_id)
            refined_bytes = OpenaiImagenClient().edit_image(cropped_bytes, prompt=prompt)
        except Exception as exc:
            err = str(exc)
            provider_errors.append(f"OpenAI: {err[:200]}")
            logger.error("Artwork %d: OpenAI API refine failed: %s", artwork_id, err)

    if refined_bytes is None:
        errors_summary = " | ".join(provider_errors) if provider_errors else "No AI provider configured"
        raise ValueError(f"All refine providers failed — {errors_summary}")
    return refined_bytes


def run_refine_job(artwork_id: int, bg_mode: str = "light") -> None:
    """BackgroundTask: AI image refinement. Transitions refining→refined|refine_failed.

    Opens its own DB session (request session closed before background task runs).
    """
    db = SessionLocal()
    artwork = None
    try:
        artwork = db.get(Artwork, artwork_id)
        if not artwork:
            logger.warning("run_refine_job: artwork %d not found", artwork_id)
            return
        if artwork.status != "refining":
            logger.warning("run_refine_job: artwork %d unexpected status '%s', skipping", artwork_id, artwork.status)
            return

        prompt = _REFINE_PROMPT_DARK if bg_mode == "dark" else _REFINE_PROMPT_LIGHT
        cropped_bytes = _load_image_bytes(artwork.cropped_url)
        refined_bytes = _call_refine_providers(artwork_id, cropped_bytes, prompt)

        refined_url = _save_and_upload(refined_bytes)
        # Reload to avoid overwriting a skip that ran concurrently
        db.refresh(artwork)
        artwork.refined_url = refined_url  # always store AI result (better than skip)
        if artwork.status == "refining":
            artwork.status = "refined"
        artwork.error_message = None
        db.commit()
        logger.info("Artwork %d: refined (status=%s), url=%s", artwork_id, artwork.status, refined_url)

    except Exception as exc:
        logger.exception("Refine job failed for artwork %d", artwork_id)
        if artwork is not None:
            db.refresh(artwork)  # reload — skip may have already resolved status
            if artwork.status == "refining":  # only fail if skip hasn't resolved it
                artwork.status = "refine_failed"
                artwork.error_message = str(exc)
                db.commit()
    finally:
        db.close()


def skip_refine_artwork(db: Session, artwork_id: int) -> Artwork:
    """Skip GPT refinement — copy cropped_url to refined_url. Accepts cropped|refine_failed|refining."""
    artwork = db.get(Artwork, artwork_id)
    if not artwork:
        raise ValueError(f"Artwork {artwork_id} not found")
    if artwork.status not in ("cropped", "refine_failed", "refining"):
        raise ValueError(f"Artwork {artwork_id} has status '{artwork.status}', expected 'cropped'")
    artwork.refined_url = artwork.cropped_url
    artwork.status = "refined"
    db.commit()
    db.refresh(artwork)
    logger.info("Artwork %d: refine skipped, using cropped_url as refined_url", artwork_id)
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

    logger.info("Artwork %d: calling remove.bg on refined image", artwork_id)
    refined_bytes = _load_image_bytes(artwork.refined_url)
    removebg_bytes = RemoveBgClient().remove_bg(refined_bytes)

    removebg_url = _save_and_upload(removebg_bytes)
    artwork.removebg_url = removebg_url
    artwork.status = "removebg_done"
    db.commit()
    db.refresh(artwork)
    logger.info("Artwork %d: removebg done, url=%s", artwork_id, removebg_url)
    return artwork


def run_upscale_job(artwork_id: int, scale: int = 4) -> None:
    """BackgroundTask: Real-ESRGAN upscale. Transitions removebg_done→upscaling→done|failed.

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
        output_bytes = _upscale_with_upscayl(input_bytes, scale=scale if scale <= 4 else 4)
        if scale > 4:
            # PIL resize from 4× to target scale (Lanczos, no extra upscayl pass needed)
            img_out = Image.open(BytesIO(output_bytes)).convert("RGBA")
            target_w = img_out.width * scale // 4
            target_h = img_out.height * scale // 4
            img_out = img_out.resize((target_w, target_h), Image.LANCZOS)
            buf = BytesIO()
            img_out.save(buf, format="PNG")
            output_bytes = buf.getvalue()
            logger.info("Artwork %d: PIL resize to %d× → %dx%d", artwork_id, scale, target_w, target_h)

        final_url = _save_and_upload(output_bytes)
        artwork.final_url = final_url
        artwork.status = "done"
        artwork.error_message = None
        db.commit()
        logger.info("Artwork %d: upscale done (status=done), url=%s", artwork_id, final_url)

    except Exception as exc:  # noqa: BLE001 — background job must not crash the server
        logger.exception("Upscale job failed for artwork %d", artwork_id)
        if artwork is not None:
            artwork.status = "failed"
            artwork.error_message = str(exc)
            db.commit()
    finally:
        db.close()


def _upscale_with_upscayl(image_bytes: bytes, scale: int = 4) -> bytes:
    """Upscale via upscayl-bin CLI. Caps input at 1024px, passes -g -1 for CPU.

    Binary path: settings.upscayl_bin (default "upscayl-bin" on PATH).
    Models dir: settings.upscayl_models (optional; uses binary's bundled default if empty).

    Raises:
        RuntimeError: Binary not found, non-zero exit code, or no output produced.
    """
    bin_path = settings.upscayl_bin or "upscayl-bin"
    if not (Path(bin_path).is_file() or shutil.which(bin_path)):
        raise RuntimeError(
            f"upscayl-bin not found at '{bin_path}'. "
            "Install Upscayl from https://github.com/upscayl/upscayl then set "
            "UPSCAYL_BIN=/path/to/upscayl-bin in .env"
        )

    # Cap input to avoid memory issues
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    if max(img.width, img.height) > _UPSCALE_MAX_INPUT_PX:
        ratio = _UPSCALE_MAX_INPUT_PX / max(img.width, img.height)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS
        )
        logger.info("Capped upscale input to %dx%d", img.width, img.height)

    input_buf = BytesIO()
    img.save(input_buf, format="PNG")

    with tempfile.TemporaryDirectory() as tmpdir:
        inp = Path(tmpdir) / "input.png"
        out = Path(tmpdir) / "output.png"
        inp.write_bytes(input_buf.getvalue())

        cmd = [bin_path, "-i", str(inp), "-o", str(out), "-s", str(scale), "-g", "0"]
        if settings.upscayl_models:
            model_name = settings.upscayl_model or "digital-art-4x"
            cmd += ["-m", settings.upscayl_models, "-n", model_name]

        logger.info("Running upscayl-bin scale=%d: %s", scale, " ".join(cmd))
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("upscayl-bin timed out after 600s") from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"upscayl-bin exited {result.returncode}. stderr: {result.stderr[:500]}"
            )
        if not out.exists():
            raise RuntimeError("upscayl-bin completed but produced no output file")

        output_bytes = out.read_bytes()
        logger.info(
            "Upscayl done: input=%d bytes → output=%d bytes",
            len(image_bytes), len(output_bytes),
        )
        return output_bytes


def fill_canvas_artwork(db: Session, artwork_id: int, canvas_w: int, canvas_h: int, fit_mode: str) -> str:
    """Place upscaled artwork on a transparent canvas at 300 DPI. Returns canvas image URL.

    Args:
        fit_mode: "width" — scale so artwork width = canvas width;
                  "height" — scale so artwork height = canvas height (fills top→bottom).
    """
    artwork = db.get(Artwork, artwork_id)
    if not artwork:
        raise ValueError(f"Artwork {artwork_id} not found")
    if not artwork.final_url:
        raise ValueError("Upscale step not complete — run Step 5 first")

    image_bytes = _load_image_bytes(artwork.final_url)
    canvas_bytes = _apply_canvas(image_bytes, canvas_w, canvas_h, fit_mode)
    return _save_and_upload(canvas_bytes)


def _apply_canvas(image_bytes: bytes, canvas_w: int, canvas_h: int, fit_mode: str) -> bytes:
    """Resize artwork to fit canvas, paste top-center on transparent background at 300 DPI."""
    img = Image.open(BytesIO(image_bytes)).convert("RGBA")

    if fit_mode == "width":
        scale = canvas_w / img.width
    else:  # height — fills top to bottom
        scale = canvas_h / img.height

    new_w = int(img.width * scale)
    new_h = int(img.height * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    # Transparent canvas, artwork aligned top-center
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    x = (canvas_w - new_w) // 2
    canvas.paste(resized, (x, 0), resized)

    buf = BytesIO()
    canvas.save(buf, format="PNG", dpi=(300, 300))
    return buf.getvalue()
