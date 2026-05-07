"""Tests for etsy_error_mapper — translates Etsy errors to user messages."""
from __future__ import annotations

import httpx

from app.services.etsy_error_mapper import map_etsy_error


def _http_status_error(status: int, body: dict | str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "/x")
    if isinstance(body, dict):
        response = httpx.Response(status, json=body, request=request)
    else:
        response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


def test_401_maps_to_login_expired():
    mapped = map_etsy_error(_http_status_error(401, {"error": "invalid_token"}))
    assert mapped.category == "auth"
    assert "login" in mapped.user_message.lower()
    assert mapped.http_status == 502


def test_429_maps_to_rate_limited():
    mapped = map_etsy_error(_http_status_error(429, {"error": "rate_limited"}))
    assert mapped.category == "rate_limit"
    assert "rate" in mapped.user_message.lower()


def test_400_with_image_word_maps_to_image_size():
    mapped = map_etsy_error(_http_status_error(400, {"error": "image must be at least 570x570"}))
    assert mapped.category == "image_size"
    assert "570" in mapped.user_message
    assert mapped.http_status == 422


def test_400_with_taxonomy_word_maps_to_taxonomy():
    mapped = map_etsy_error(_http_status_error(400, {"error": "invalid taxonomy value"}))
    assert mapped.category == "taxonomy"
    assert mapped.http_status == 422


def test_5xx_maps_to_etsy_5xx():
    mapped = map_etsy_error(_http_status_error(503, "service down"))
    assert mapped.category == "etsy_5xx"
    assert "issues" in mapped.user_message.lower() or "down" in mapped.user_message.lower()


def test_unknown_4xx_falls_through_to_other():
    mapped = map_etsy_error(_http_status_error(409, "conflict"))
    assert mapped.category == "other"
    assert "409" in mapped.user_message


def test_non_httpx_exception_returns_unknown():
    mapped = map_etsy_error(RuntimeError("network blew up"))
    assert mapped.category == "unknown"
    assert "network blew up" in mapped.user_message
