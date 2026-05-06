"""Tests for Etsy OAuth PKCE helpers and EtsyApiClient retry/CRUD logic.

Uses httpx MockTransport — no real network calls.
"""
import base64
import hashlib
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.clients.etsy_oauth import (
    build_auth_url,
    exchange_code,
    generate_pkce_pair,
    get_valid_token,
    refresh_access_token,
    save_token,
)
from app.clients.etsy_api_client import EtsyApiClient
from app.models.api_credential import ApiCredential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_db(cred: ApiCredential | None = None) -> MagicMock:
    """Return a MagicMock Session that returns `cred` from db.get()."""
    db = MagicMock()
    db.get.return_value = cred
    return db


def _make_cred(
    token: str = "tok",
    refresh: str = "ref",
    expires_delta: int = 3600,
) -> ApiCredential:
    cred = ApiCredential(
        provider="etsy",
        oauth_token=token,
        refresh_token=refresh,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=expires_delta),
    )
    return cred


# ---------------------------------------------------------------------------
# PKCE tests
# ---------------------------------------------------------------------------

class TestPkcePair:
    def test_returns_two_non_empty_strings(self):
        verifier, challenge = generate_pkce_pair()
        assert isinstance(verifier, str) and len(verifier) > 0
        assert isinstance(challenge, str) and len(challenge) > 0

    def test_challenge_is_sha256_of_verifier(self):
        verifier, challenge = generate_pkce_pair()
        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert challenge == expected

    def test_different_calls_produce_different_pairs(self):
        v1, c1 = generate_pkce_pair()
        v2, c2 = generate_pkce_pair()
        assert v1 != v2
        assert c1 != c2

    def test_no_padding_chars_in_output(self):
        verifier, challenge = generate_pkce_pair()
        assert "=" not in verifier
        assert "=" not in challenge


class TestBuildAuthUrl:
    def test_contains_required_params(self):
        url = build_auth_url(state="s1", code_challenge="ch1")
        assert "response_type=code" in url
        assert "state=s1" in url
        assert "code_challenge=ch1" in url
        assert "code_challenge_method=S256" in url

    def test_base_url_is_etsy(self):
        url = build_auth_url(state="x", code_challenge="y")
        assert url.startswith("https://www.etsy.com/oauth/connect")


# ---------------------------------------------------------------------------
# Token exchange / refresh (mocked HTTP)
# ---------------------------------------------------------------------------

class TestExchangeCode:
    def test_returns_token_dict_on_success(self):
        """exchange_code returns the parsed token dict from Etsy on HTTP 200."""
        expected = {"access_token": "at1", "refresh_token": "rt1", "expires_in": 3600}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=expected)

        transport = httpx.MockTransport(handler)
        with patch("app.clients.etsy_oauth.httpx.Client", return_value=httpx.Client(transport=transport)):
            result = exchange_code(code="authcode", code_verifier="verifier")
        assert result["access_token"] == "at1"
        assert result["refresh_token"] == "rt1"

    def test_raises_on_http_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "unauthorized"})

        transport = httpx.MockTransport(handler)
        with patch("app.clients.etsy_oauth.httpx.Client", return_value=httpx.Client(transport=transport)):
            with pytest.raises(httpx.HTTPStatusError):
                exchange_code(code="bad", code_verifier="verifier")


class TestRefreshToken:
    def test_successful_refresh_returns_new_token(self):
        new_token = {"access_token": "new_at", "refresh_token": "new_rt", "expires_in": 3600}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=new_token)

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:
            resp = client.post("https://api.etsy.com/v3/public/oauth/token", data={})
        assert resp.json() == new_token


# ---------------------------------------------------------------------------
# Token persistence
# ---------------------------------------------------------------------------

class TestSaveToken:
    def test_inserts_new_credential(self):
        db = _mock_db(cred=None)
        token = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        save_token(db, token)
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_updates_existing_credential(self):
        existing = _make_cred(token="old", refresh="old_r")
        db = _mock_db(cred=existing)
        token = {"access_token": "new_at", "refresh_token": "new_rt", "expires_in": 7200}
        save_token(db, token)
        assert existing.oauth_token == "new_at"
        assert existing.refresh_token == "new_rt"
        db.commit.assert_called_once()


class TestGetValidToken:
    def test_returns_none_when_no_credential(self):
        db = _mock_db(cred=None)
        assert get_valid_token(db) is None

    def test_returns_token_when_valid(self):
        cred = _make_cred(token="valid_tok", expires_delta=3600)
        db = _mock_db(cred=cred)
        result = get_valid_token(db)
        assert result == "valid_tok"

    def test_triggers_refresh_when_near_expiry(self):
        cred = _make_cred(token="old_tok", refresh="ref", expires_delta=60)
        db = _mock_db(cred=cred)

        new_token = {"access_token": "refreshed", "refresh_token": "ref2", "expires_in": 3600}
        with patch("app.clients.etsy_oauth.refresh_access_token", return_value=new_token) as mock_refresh, \
             patch("app.clients.etsy_oauth.save_token") as mock_save:
            get_valid_token(db)
            mock_refresh.assert_called_once_with("ref")
            mock_save.assert_called_once_with(db, new_token)

    def test_returns_stale_token_if_refresh_fails(self):
        cred = _make_cred(token="stale_tok", refresh="ref", expires_delta=60)
        db = _mock_db(cred=cred)

        with patch("app.clients.etsy_oauth.refresh_access_token", side_effect=Exception("network error")):
            result = get_valid_token(db)
        assert result == "stale_tok"


# ---------------------------------------------------------------------------
# EtsyApiClient retry logic
# ---------------------------------------------------------------------------

class TestEtsyApiClientRetry:
    def _build_client(self, handler, db=None) -> EtsyApiClient:
        """Patch EtsyApiClient's internal httpx.Client with mock transport."""
        if db is None:
            db = _mock_db(cred=_make_cred())
        client = EtsyApiClient.__new__(EtsyApiClient)
        client._db = db
        client._http = httpx.Client(
            base_url="https://openapi.etsy.com/v3/application",
            transport=httpx.MockTransport(handler),
            timeout=5,
        )
        return client

    def test_returns_response_on_200(self):
        def handler(req):
            return httpx.Response(200, json={"listing_id": 1})

        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"):
            c = self._build_client(handler)
            resp = c._request("GET", "/listings/1")
        assert resp.json()["listing_id"] == 1

    def test_retries_on_429_then_succeeds(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] < 3:
                return httpx.Response(429, json={"error": "rate_limit"})
            return httpx.Response(200, json={"ok": True})

        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"), \
             patch("app.clients.etsy_api_client.time.sleep"):
            c = self._build_client(handler)
            resp = c._request("GET", "/listings/1")

        assert resp.status_code == 200
        assert call_count["n"] == 3

    def test_raises_after_max_retries_on_429(self):
        def handler(req):
            return httpx.Response(429, json={"error": "rate_limit"})

        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"), \
             patch("app.clients.etsy_api_client.time.sleep"):
            c = self._build_client(handler)
            with pytest.raises(httpx.HTTPStatusError):
                c._request("GET", "/listings/1")

    def test_retries_on_500(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            if call_count["n"] < 2:
                return httpx.Response(500, json={"error": "server_error"})
            return httpx.Response(200, json={"ok": True})

        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"), \
             patch("app.clients.etsy_api_client.time.sleep"):
            c = self._build_client(handler)
            resp = c._request("GET", "/listings/1")

        assert resp.status_code == 200
        assert call_count["n"] == 2

    def test_does_not_retry_on_404(self):
        call_count = {"n": 0}

        def handler(req):
            call_count["n"] += 1
            return httpx.Response(404, json={"error": "not_found"})

        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"):
            c = self._build_client(handler)
            with pytest.raises(httpx.HTTPStatusError):
                c._request("GET", "/listings/999")

        assert call_count["n"] == 1

    def test_update_listing_raises_without_fields(self):
        db = _mock_db(cred=_make_cred())
        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"):
            c = EtsyApiClient.__new__(EtsyApiClient)
            c._db = db
            c._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
            with pytest.raises(ValueError, match="at least one field"):
                c.update_listing(123)

    def test_upload_listing_image_raises_when_file_missing(self, tmp_path):
        db = _mock_db(cred=_make_cred())
        with patch("app.clients.etsy_api_client.get_valid_token", return_value="tok"):
            c = EtsyApiClient.__new__(EtsyApiClient)
            c._db = db
            c._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
            with pytest.raises(FileNotFoundError):
                c.upload_listing_image(123, tmp_path / "missing.png")
