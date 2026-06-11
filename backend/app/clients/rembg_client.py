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

from app.config import settings

logger = logging.getLogger(__name__)

_session = None        # lazy-loaded to avoid slowing server startup
_session_model = None  # model name the cached session was built with
_inference_lock = threading.Lock()  # serializes session creation + inference


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


class RembgClient:
    """Local background removal — $0/image, ~37s on 6-core CPU (birefnet-general)."""

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """Remove background. Returns transparent PNG bytes.

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
        logger.info("rembg: %d → %d bytes", len(image_bytes), len(result))
        return result
