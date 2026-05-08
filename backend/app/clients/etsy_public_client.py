"""Etsy public API v3 client — keyword search + listing detail (x-api-key only, no OAuth).

Distinct from EtsyApiClient which requires an OAuth Bearer token for shop-write operations.
This client only needs x-api-key and is used by the idea miner to read public listings.

Throttle: 200ms between calls (Etsy public rate: 10/sec, 10K/day).
Retry: up to MAX_RETRIES on 429 and 5xx with exponential backoff.
Dry-run: when settings.etsy_dry_run=True, short-circuits to etsy_public_dry_run_fixtures.
"""
from __future__ import annotations

import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.etsy.com/v3/application"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds
THROTTLE_SEC = 0.2     # 200ms between calls


class EtsyPublicClient:
    """Synchronous Etsy public API v3 client.

    Injects x-api-key header on every request.
    Retries up to MAX_RETRIES times with exponential backoff on 429 / 5xx.
    Logs method, path, status, and duration for every call.
    No OAuth needed — public listing search requires only the API key.
    """

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or settings.etsy_api_key or ""
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-api-key": key},
            timeout=30,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EtsyPublicClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal request with retry + throttle
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request with retry logic.

        Retries on 429 (rate-limit) and 5xx (server error) with exponential backoff.
        Raises httpx.HTTPStatusError if all retries exhausted.
        """
        backoff = INITIAL_BACKOFF
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            start = time.perf_counter()
            try:
                resp = self._http.request(method, path, **kwargs)
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.info(
                    "etsy_public %s %s → %d (%dms) attempt=%d",
                    method,
                    path,
                    resp.status_code,
                    duration_ms,
                    attempt,
                )

                # Success or non-retryable client error
                if resp.status_code < 429:
                    resp.raise_for_status()
                    return resp

                # 429 or 5xx — retryable
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt == MAX_RETRIES:
                        resp.raise_for_status()
                    retry_after = resp.headers.get("Retry-After")
                    sleep_sec = float(retry_after) if retry_after else backoff
                    logger.warning(
                        "etsy_public retryable %d on %s %s, backoff=%.1fs",
                        resp.status_code, method, path, sleep_sec,
                    )
                    time.sleep(sleep_sec)
                    backoff *= 2
                    continue

                # Other 4xx — not retryable
                resp.raise_for_status()
                return resp

            except httpx.TransportError as exc:
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.warning(
                    "etsy_public transport error %s %s (%dms) attempt=%d: %s",
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
    # Public search
    # ------------------------------------------------------------------

    def search_active_listings(
        self,
        keywords: str,
        limit: int = 100,
        sort_on: str = "score",
        taxonomy_id: int | None = None,
    ) -> list[dict]:
        """Search public active listings by keyword.

        Wraps GET /v3/application/listings/active.
        When settings.etsy_dry_run is True, returns fixture results without HTTP.

        Args:
            keywords: Search term(s), space-separated.
            limit: Max results to return (Etsy max=100 per page).
            sort_on: Etsy sort field — "score" (relevance), "created", "price".
            taxonomy_id: Optional Etsy taxonomy node ID to filter by category.

        Returns:
            List of listing summary dicts from Etsy ``results`` array.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_public_dry_run_fixtures
            result = etsy_public_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "search_active_listings",
                {"keywords": keywords, "limit": limit},
            )
            return result.get("results", [])

        params: dict = {
            "keywords": keywords,
            "limit": min(limit, 100),
            "sort_on": sort_on,
            "sort_order": "desc",
        }
        if taxonomy_id is not None:
            params["taxonomy_id"] = taxonomy_id

        resp = self._request("GET", "/listings/active", params=params)
        time.sleep(THROTTLE_SEC)
        return resp.json().get("results", [])

    # ------------------------------------------------------------------
    # Listing detail
    # ------------------------------------------------------------------

    def get_listing(self, listing_id: str | int) -> dict:
        """Fetch full listing detail including images and tags.

        Wraps GET /v3/application/listings/{listing_id}?includes=Images.
        When settings.etsy_dry_run is True, returns fixture detail without HTTP.

        Args:
            listing_id: Etsy listing ID (numeric string or int).

        Returns:
            Single listing detail dict.
        """
        if settings.etsy_dry_run:
            from app.clients import etsy_public_dry_run_fixtures
            return etsy_public_dry_run_fixtures.dispatch(
                settings.etsy_dry_run_scenario,
                "get_listing",
                {"listing_id": str(listing_id)},
            )

        resp = self._request(
            "GET",
            f"/listings/{listing_id}",
            params={"includes": "Images"},
        )
        time.sleep(THROTTLE_SEC)
        return resp.json()
