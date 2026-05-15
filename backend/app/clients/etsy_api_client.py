"""Etsy Open API v3 client — CRUD for listings with retry/backoff and rate-limit awareness."""
import logging
import time
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.clients.etsy_oauth import get_valid_token
from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.etsy.com/v3/application"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds


class EtsyApiClient:
    """Synchronous Etsy API v3 client.

    Injects x-api-key + Bearer token on every request.
    Retries up to MAX_RETRIES times with exponential backoff on 429 / 5xx.
    Logs method, path, status, and duration for every call.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._http = httpx.Client(
            base_url=BASE_URL,
            # Etsy v3 requires x-api-key in `keystring:shared_secret` format.
            headers={"x-api-key": f"{settings.etsy_api_key}:{settings.etsy_shared_secret}"},
            timeout=30,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EtsyApiClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal request with retry logic
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request with Bearer token injection and retry logic.

        Retries on 429 (rate-limit) and 5xx (server error) with exponential backoff.
        Raises httpx.HTTPStatusError if all retries exhausted.
        """
        token = get_valid_token(self._db)
        if token:
            headers = kwargs.pop("headers", {})
            headers["Authorization"] = f"Bearer {token}"
            kwargs["headers"] = headers

        backoff = INITIAL_BACKOFF
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                resp = self._http.request(method, path, **kwargs)
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.info(
                    "etsy_api %s %s → %d (%dms) attempt=%d",
                    method,
                    path,
                    resp.status_code,
                    duration_ms,
                    attempt,
                )

                # Success or client error (not retryable)
                if resp.status_code < 429:
                    if resp.status_code >= 400:
                        # Log Etsy's error body so callers can diagnose 4xx.
                        logger.error(
                            "etsy_api %s %s body: %s",
                            method, path, (resp.text or "")[:2000],
                        )
                    resp.raise_for_status()
                    return resp

                # 429 or 5xx — retryable
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt == MAX_RETRIES:
                        resp.raise_for_status()
                    logger.warning(
                        "etsy_api retryable %d on %s %s, backoff=%.1fs",
                        resp.status_code, method, path, backoff,
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue

                # Other 4xx — not retryable
                resp.raise_for_status()
                return resp

            except httpx.TransportError as exc:
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.warning(
                    "etsy_api transport error %s %s (%dms) attempt=%d: %s",
                    method, path, duration_ms, attempt, exc,
                )
                last_exc = exc
                if attempt == MAX_RETRIES:
                    raise
                time.sleep(backoff)
                backoff *= 2

        # Should not reach here
        if last_exc:
            raise last_exc
        raise RuntimeError("_request exhausted retries without raising")  # pragma: no cover

    # ------------------------------------------------------------------
    # Shop / user
    # ------------------------------------------------------------------

    def get_user_shops(self, user_id: str) -> dict:
        """Return shops belonging to a Etsy user (GET /users/{user_id}/shops)."""
        return self._request("GET", f"/users/{user_id}/shops").json()

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def list_active_listings(self, shop_id: str | int, limit: int = 100) -> list[dict]:
        """Paginate through all active listings for a shop.

        Returns a flat list of listing dicts.
        Etsy max page size = 100; iterates until no more results.
        """
        results: list[dict] = []
        offset = 0

        while True:
            resp = self._request(
                "GET",
                f"/shops/{shop_id}/listings/active",
                params={"limit": limit, "offset": offset},
            )
            body = resp.json()
            batch = body.get("results", [])
            results.extend(batch)

            if len(batch) < limit:
                break
            offset += limit

        return results

    def get_listing(self, listing_id: str | int, includes: list[str] | None = None) -> dict:
        """Fetch a single listing by ID, optionally with sub-resources.

        includes example: ["Images", "MainImage", "Shop"]
        """
        params: dict = {}
        if includes:
            params["includes"] = ",".join(includes)
        return self._request("GET", f"/listings/{listing_id}", params=params).json()

    def update_listing(self, listing_id: str | int, **fields) -> dict:
        """PATCH a listing — only send fields that need updating.

        Accepted fields: title, description, tags, price, quantity, ...
        Ref: https://developers.etsy.com/documentation/reference#operation/updateListing
        """
        if not fields:
            raise ValueError("update_listing requires at least one field to update")
        return self._request("PATCH", f"/listings/{listing_id}", json=fields).json()

    def delete_listing(self, shop_id: str | int, listing_id: str | int) -> None:
        """DELETE /shops/{shop_id}/listings/{listing_id}.

        Etsy soft-deletes the listing (state becomes 'inactive', does not vanish).
        Idempotent: 404 (already gone) is treated as success.
        Ref: https://developers.etsy.com/documentation/reference#operation/deleteListing
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "delete_listing",
                {"shop_id": shop_id, "listing_id": listing_id},
            )
            return
        try:
            self._request("DELETE", f"/shops/{shop_id}/listings/{listing_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info("delete_listing %s: already gone (404)", listing_id)
                return
            raise

    def get_listing(
        self,
        listing_id: str | int,
        *,
        includes: tuple[str, ...] = ("Images", "Inventory"),
    ) -> dict:
        """GET /listings/{listing_id}?includes=...

        Returns full Etsy payload for sync — title, description, tags, state,
        plus optional Images + Inventory sub-resources.
        Ref: https://developers.etsy.com/documentation/reference#operation/getListing
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "get_listing",
                {"listing_id": listing_id},
            )
        params = {"includes": ",".join(includes)} if includes else {}
        return self._request("GET", f"/listings/{listing_id}", params=params).json()

    # ------------------------------------------------------------------
    # Image upload
    # ------------------------------------------------------------------

    def upload_listing_image(
        self,
        listing_id: str | int,
        image_path: str | Path,
        rank: int = 1,
    ) -> dict:
        """Upload an image file to a listing (multipart POST).

        rank=1 is the primary image. Max 20 images per listing.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "upload_listing_image",
                {"listing_id": listing_id, "rank": rank},
            )
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        with image_path.open("rb") as fh:
            files = {"image": (image_path.name, fh, "image/png")}
            data = {"rank": str(rank)}
            return self._request(
                "POST",
                f"/listings/{listing_id}/images",
                files=files,
                data=data,
            ).json()

    # ------------------------------------------------------------------
    # Listing creation (sub-feature C)
    # ------------------------------------------------------------------

    def create_draft_listing(
        self,
        shop_id: str | int,
        *,
        title: str,
        description: str,
        price: float,
        quantity: int,
        taxonomy_id: int,
        tags: list[str] | None = None,
        who_made: str = "i_did",
        when_made: str = "made_to_order",
        is_supply: bool = False,
        **extra,
    ) -> dict:
        """Create a draft listing (state='draft' until seller publishes).

        Etsy ref: POST /shops/{shop_id}/listings (draftListing).
        Returns the created listing dict including `listing_id`.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "create_draft_listing",
                {"shop_id": shop_id, "title": title, "taxonomy_id": taxonomy_id, **extra},
            )

        body: dict = {
            "title": title,
            "description": description,
            "price": price,
            "quantity": quantity,
            "taxonomy_id": taxonomy_id,
            "who_made": who_made,
            "when_made": when_made,
            "is_supply": "true" if is_supply else "false",
            "state": "draft",
        }
        if tags:
            # Etsy: max 13 tags, each ≤20 chars. Drop over-length tags.
            clean_tags = [t.strip() for t in tags if t and len(t.strip()) <= 20]
            if len(clean_tags) != len(tags):
                logger.warning(
                    "create_draft_listing dropped %d tag(s) > 20 chars",
                    len(tags) - len(clean_tags),
                )
            if clean_tags:
                body["tags"] = ",".join(clean_tags[:13])
        body.update(extra)

        # Etsy v3 wants form-encoded for create
        return self._request(
            "POST",
            f"/shops/{shop_id}/listings",
            data=body,
        ).json()

    def update_listing_inventory(
        self,
        listing_id: str | int,
        products: list[dict],
        *,
        price_on_property: list[int] | None = None,
        quantity_on_property: list[int] | None = None,
        sku_on_property: list[int] | None = None,
    ) -> dict:
        """Replace listing inventory with full variations matrix.

        Each product in `products` is:
            {
                "sku": "...",  # optional
                "property_values": [
                    {"property_id": 200, "value_id": 1, "value": "White", ...},
                    {"property_id": 506, "value_id": 4, "value": "M", ...},
                ],
                "offerings": [{"price": 19.0, "quantity": 100, "is_enabled": True}],
            }

        Etsy ref: PUT /listings/{listing_id}/inventory.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "update_listing_inventory",
                {"listing_id": listing_id, "products": products},
            )
        body = {
            "products": products,
            "price_on_property": price_on_property or [],
            "quantity_on_property": quantity_on_property or [],
            "sku_on_property": sku_on_property or [],
        }
        return self._request(
            "PUT",
            f"/listings/{listing_id}/inventory",
            json=body,
        ).json()

    def upload_listing_image_bytes(
        self,
        shop_id: str | int,
        listing_id: str | int,
        image_bytes: bytes,
        *,
        filename: str = "mockup.png",
        rank: int = 1,
    ) -> dict:
        """Upload an in-memory image to a listing (multipart POST), with rank.

        Etsy ref: POST /shops/{shop_id}/listings/{listing_id}/images.
        rank=1 is the primary image, used as the listing's main thumbnail.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "upload_listing_image_bytes",
                {"shop_id": shop_id, "listing_id": listing_id, "rank": rank, "filename": filename},
            )
        files = {"image": (filename, image_bytes, "image/png")}
        data = {"rank": str(rank)}
        return self._request(
            "POST",
            f"/shops/{shop_id}/listings/{listing_id}/images",
            files=files,
            data=data,
        ).json()

    def set_variation_images(
        self,
        shop_id: str | int,
        listing_id: str | int,
        property_id: int,
        value_to_image_id: dict[int, int],
    ) -> dict:
        """Map variation values to listing images for a single property.

        Etsy ref: POST /shops/{shop_id}/listings/{listing_id}/variation-images.
        ``value_to_image_id`` maps each value_id to the etsy_image_id that should
        represent it in the storefront (e.g. colour swatch → mockup image).
        Returns the full variation-images response from Etsy.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "set_variation_images",
                {
                    "shop_id": shop_id,
                    "listing_id": listing_id,
                    "property_id": property_id,
                    "value_to_image_id": value_to_image_id,
                },
            )

        body = {
            "variation_images": [
                {"property_id": property_id, "value_id": value_id, "image_id": image_id}
                for value_id, image_id in value_to_image_id.items()
            ]
        }
        result = self._request(
            "POST",
            f"/shops/{shop_id}/listings/{listing_id}/variation-images",
            json=body,
        ).json()

        missing = len(value_to_image_id) - len(result.get("results", []))
        if missing > 0:
            logger.warning(
                "set_variation_images: sent %d mappings but got %d back (listing_id=%s property_id=%s)",
                len(value_to_image_id),
                len(result.get("results", [])),
                listing_id,
                property_id,
            )
        else:
            logger.info(
                "set_variation_images: linked %d image(s) for listing_id=%s property_id=%s",
                len(value_to_image_id),
                listing_id,
                property_id,
            )
        return result

    # ------------------------------------------------------------------
    # Taxonomy
    # ------------------------------------------------------------------

    def get_taxonomy_property_values(
        self,
        taxonomy_id: int,
        property_id: int,
    ) -> dict:
        """List allowed value_ids for a property within a taxonomy node.

        Etsy ref: GET /seller-taxonomy/nodes/{taxonomy_id}/properties — returns
        every property for the node. Etsy has no per-property-id sub-endpoint,
        so we fetch the full list and filter for ``property_id`` locally.
        Returns a dict shaped ``{"possible_values": [...]}`` for the match
        (empty list when the property is absent).
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_dry_run_fixtures
            return etsy_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "get_taxonomy_property_values",
                {"taxonomy_id": taxonomy_id, "property_id": property_id},
            )
        payload = self._request(
            "GET",
            f"/seller-taxonomy/nodes/{taxonomy_id}/properties",
        ).json()
        for prop in payload.get("results", []) or []:
            if int(prop.get("property_id", -1)) == int(property_id):
                return {
                    "name": prop.get("name") or "",
                    "scales": prop.get("scales") or [],
                    "possible_values": prop.get("possible_values") or [],
                }
        return {"name": "", "scales": [], "possible_values": []}
