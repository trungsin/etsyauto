"""Background-removal provider selection — PhotoRoom preferred, remove.bg fallback.

All providers expose the same interface: ``remove_bg(image_bytes) -> bytes``
(transparent PNG). Call sites use :func:`get_bg_removal_client`; provider
choice is a pure .env change (set PHOTOROOM_API_KEY to prefer PhotoRoom).
If the preferred provider fails (e.g. PhotoRoom 402 — plan not active),
the chain automatically falls through to the next configured provider.
"""
import logging

from app.clients.photoroom_client import PhotoRoomClient
from app.clients.removebg_client import RemoveBgClient
from app.config import settings

logger = logging.getLogger(__name__)


class ChainBgRemovalClient:
    """Tries each configured provider in order; returns first success."""

    def __init__(self, clients: list) -> None:
        if not clients:
            raise ValueError("No background-removal provider configured "
                             "(set PHOTOROOM_API_KEY or REMOVEBG_API_KEYS)")
        self._clients = clients

    def remove_bg(self, image_bytes: bytes) -> bytes:
        last_error: Exception | None = None
        for client in self._clients:
            name = type(client).__name__
            try:
                result = client.remove_bg(image_bytes)
                logger.info("Background removal via %s", name)
                return result
            except Exception as exc:  # noqa: BLE001 — fall through to next provider
                logger.warning("%s failed (%s) — trying next provider", name, exc)
                last_error = exc
        raise last_error  # type: ignore[misc]


def get_bg_removal_client() -> ChainBgRemovalClient:
    """Build provider chain from .env: PhotoRoom first (if key set), then remove.bg."""
    clients: list = []
    if settings.photoroom_api_keys or settings.photoroom_api_key:
        clients.append(PhotoRoomClient())
    if settings.removebg_api_keys or settings.removebg_api_key or settings.removebg_api_key_backup:
        clients.append(RemoveBgClient())
    return ChainBgRemovalClient(clients)
