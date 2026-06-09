"""Local background removal using rembg + ONNX Runtime — free, no API cost.

Model: isnet-general-use (~170 MB, downloaded on first use to ~/.u2net/).
Works well for designs on clean white/dark backgrounds from the refine step.
"""
import logging

logger = logging.getLogger(__name__)

_MODEL = "isnet-general-use"
_session = None  # lazy-loaded to avoid slowing server startup


def _get_session():
    global _session  # noqa: PLW0603
    if _session is None:
        from rembg import new_session
        logger.info("Loading rembg model '%s' (first run downloads ~170 MB)…", _MODEL)
        _session = new_session(_MODEL)
        logger.info("rembg model ready")
    return _session


class RembgClient:
    """Local background removal — $0/image, ~10-15s on 6-core CPU."""

    def remove_bg(self, image_bytes: bytes) -> bytes:
        """Remove background. Returns transparent PNG bytes.

        Raises:
            ImportError: If rembg/onnxruntime not installed.
            RuntimeError: On inference failure.
        """
        from rembg import remove
        session = _get_session()
        result = remove(image_bytes, session=session)
        if not result:
            raise RuntimeError("rembg returned empty result")
        logger.info("rembg: %d → %d bytes", len(image_bytes), len(result))
        return result
