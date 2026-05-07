"""Tests for the correlation-id middleware (X-Request-ID round-trip)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_response_carries_x_request_id_header():
    """Every response should echo a generated X-Request-ID."""
    with TestClient(app) as c:
        resp = c.get("/health")
    assert "x-request-id" in {k.lower() for k in resp.headers.keys()}
    assert resp.headers.get("X-Request-ID")  # non-empty


def test_inbound_x_request_id_is_preserved():
    """If the caller supplies its own X-Request-ID, the server reuses it."""
    cid = "abc123def456"
    with TestClient(app) as c:
        resp = c.get("/health", headers={"X-Request-ID": cid})
    assert resp.headers.get("X-Request-ID") == cid


def test_distinct_requests_get_distinct_ids():
    with TestClient(app) as c:
        a = c.get("/health").headers.get("X-Request-ID")
        b = c.get("/health").headers.get("X-Request-ID")
    assert a and b and a != b
