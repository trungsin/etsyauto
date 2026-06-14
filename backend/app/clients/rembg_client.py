"""Local background removal using rembg + ONNX Runtime — free, no API cost.

Model is configurable via REMBG_MODEL (default: birefnet-general, ~930MB
downloaded on first use to ~/.u2net/; cleanest edges, ~37s/img on 6-core CPU).

Inference is serialized with a module-level lock: one inference saturates all
CPU cores, so concurrent calls would thrash the CPU and double RAM. Acceptable
for a single-user local-first app; bursts queue behind the lock.
"""
import logging
import threading
import time
from io import BytesIO

from app.config import settings

logger = logging.getLogger(__name__)

_session = None        # lazy-loaded to avoid slowing server startup
_session_model = None  # model name the cached session was built with
_inference_lock = threading.Lock()  # serializes session creation + inference

# Alpha-cleanup tuning. Faint pixels below this alpha are background haze → drop.
# Real anti-aliased edges sit well above 12, so they survive.
_ALPHA_FAINT_THRESHOLD = 12
# Detached blobs smaller than this fraction of the largest component are stray
# background scraps rembg mislabeled as foreground → drop. Connected thin
# details survive (they belong to the main component).
_ALPHA_MIN_ISLAND_FRACTION = 0.01


def _get_session():
    """Return cached ONNX session for the configured model (lazy singleton)."""
    global _session, _session_model  # noqa: PLW0603
    model = settings.rembg_model
    if _session is None or _session_model != model:
        from rembg import new_session
        logger.info("Loading rembg model '%s' (first run downloads it to ~/.u2net/)…", model)
        _session = new_session(model)
        _session_model = model
        logger.info("rembg model '%s' ready", model)
    return _session


def warmup() -> None:
    """Pre-load the model session (call from a daemon thread at startup).

    Best-effort: failures are logged, never raised — the chain falls back to
    API providers if rembg is unusable.
    """
    started = time.monotonic()
    logger.info("rembg warmup starting (model '%s')…", settings.rembg_model)
    try:
        with _inference_lock:
            _get_session()
        logger.info("rembg warmup done in %.1fs", time.monotonic() - started)
    except Exception:  # noqa: BLE001 — warmup must never crash startup
        logger.exception("rembg warmup failed — first request will retry or fall back to API")


def _clean_alpha(png_bytes: bytes) -> bytes:
    """Clean the alpha mask: drop faint background haze + detached stray blobs.

    Two passes on the alpha channel:
      1. Faint-haze threshold — alpha < _ALPHA_FAINT_THRESHOLD → 0.
      2. Island removal — keep the largest connected component (the main
         design); zero any detached blob smaller than _ALPHA_MIN_ISLAND_FRACTION
         of it. Thin details connected to the design survive.

    Best-effort: on any failure, returns the original bytes unchanged so the
    provider chain is never broken.
    """
    try:
        import numpy as np
        from PIL import Image
        from scipy import ndimage

        img = Image.open(BytesIO(png_bytes)).convert("RGBA")
        arr = np.array(img)
        alpha = arr[:, :, 3]

        # Pass 1: drop faint background haze.
        alpha[alpha < _ALPHA_FAINT_THRESHOLD] = 0

        # Pass 2: remove detached stray blobs.
        mask = alpha > 0
        labels, count = ndimage.label(mask)
        if count > 1:
            # component_sizes[0] is the background (label 0) — ignore it.
            sizes = ndimage.sum(mask, labels, range(1, count + 1))
            largest = sizes.max()
            min_size = largest * _ALPHA_MIN_ISLAND_FRACTION
            # Labels (1-indexed) whose component is too small → strip.
            small_labels = {i + 1 for i, s in enumerate(sizes) if s < min_size}
            if small_labels:
                strip = np.isin(labels, list(small_labels))
                alpha[strip] = 0
                logger.info("rembg alpha cleanup: removed %d stray blob(s)", len(small_labels))

        arr[:, :, 3] = alpha
        buf = BytesIO()
        Image.fromarray(arr, "RGBA").save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — cleanup is best-effort, never break the chain
        logger.exception("rembg alpha cleanup failed — using raw rembg output")
        return png_bytes


class RembgClient:
    """Local background removal — $0/image, ~37s on 6-core CPU (birefnet-general)."""

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """Remove background, then clean the alpha mask. Returns transparent PNG.

        Raises:
            ImportError: If rembg/onnxruntime not installed.
            RuntimeError: On inference failure.
        """
        from rembg import remove
        with _inference_lock:
            session = _get_session()
            result = remove(image_bytes, session=session)
        if not result:
            raise RuntimeError("rembg returned empty result")
        result = _clean_alpha(result)
        logger.info("rembg: %d → %d bytes", len(image_bytes), len(result))
        return result
