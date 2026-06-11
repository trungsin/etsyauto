"""Background-removal provider selection — local rembg primary, APIs fallback.

All providers expose the same interface: ``remove_bg(image_bytes) -> bytes``
(transparent PNG). Call sites use :func:`get_bg_removal_client`. Chain order:
local rembg first (always present, $0/image, no key needed), then PhotoRoom
and remove.bg as API fallbacks when their keys are configured. If a provider
fails, the chain automatically falls through to the next one.
"""
import logging

from app.clients.photoroom_client import PhotoRoomClient
from app.clients.rembg_client import RembgClient
from app.clients.removebg_client import RemoveBgClient
from app.config import settings

logger = logging.getLogger(__name__)


class ChainBgRemovalClient:
    """Tries each configured provider in order; returns first success."""

    def __init__(self, clients: list) -> None:
        if not clients:
            # Unreachable via get_bg_removal_client() (rembg always present);
            # kept defensive for direct construction.
            raise ValueError("No background-removal provider in chain")
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
    """Build provider chain: local rembg always first ($0), then API providers
    (PhotoRoom, remove.bg) appended only when their keys are configured."""
    clients: list = [RembgClient()]
    if settings.photoroom_api_keys or settings.photoroom_api_key:
        clients.append(PhotoRoomClient())
    if settings.removebg_api_keys or settings.removebg_api_key or settings.removebg_api_key_backup:
        clients.append(RemoveBgClient())
    return ChainBgRemovalClient(clients)
