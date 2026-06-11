"""Tests for the BG-removal provider chain: rembg-first order, fallback,
model-from-settings, and inference lock. No real model load/inference —
rembg internals are patched at the module boundary (CI-safe, fast)."""
from unittest.mock import MagicMock, patch

import pytest

import app.clients.bg_removal as bg_removal
import app.clients.rembg_client as rembg_client
from app.clients.bg_removal import ChainBgRemovalClient, get_bg_removal_client


# ── chain construction ───────────────────────────────────────────────────────

def _patch_keys(monkeypatch, photoroom: str = "", removebg: str = "") -> None:
    """Set/clear all key variants the chain builder inspects."""
    s = bg_removal.settings
    monkeypatch.setattr(s, "photoroom_api_keys", photoroom)
    monkeypatch.setattr(s, "photoroom_api_key", "")
    monkeypatch.setattr(s, "removebg_api_keys", removebg)
    monkeypatch.setattr(s, "removebg_api_key", "")
    monkeypatch.setattr(s, "removebg_api_key_backup", "")


def test_chain_order_all_keys_set(monkeypatch):
    _patch_keys(monkeypatch, photoroom="pk", removebg="rk")
    chain = get_bg_removal_client()
    names = [type(c).__name__ for c in chain._clients]
    assert names == ["RembgClient", "PhotoRoomClient", "RemoveBgClient"]


def test_chain_rembg_only_when_no_api_keys(monkeypatch):
    _patch_keys(monkeypatch)
    chain = get_bg_removal_client()
    names = [type(c).__name__ for c in chain._clients]
    assert names == ["RembgClient"]


def test_empty_chain_raises():
    with pytest.raises(ValueError, match="No background-removal provider"):
        ChainBgRemovalClient([])


# ── fallback behaviour ───────────────────────────────────────────────────────

def test_rembg_failure_falls_through_to_next_provider():
    failing = MagicMock()
    failing.remove_bg.side_effect = RuntimeError("model corrupted")
    succeeding = MagicMock()
    succeeding.remove_bg.return_value = b"png-bytes"
    chain = ChainBgRemovalClient([failing, succeeding])
    assert chain.remove_bg(b"img") == b"png-bytes"
    failing.remove_bg.assert_called_once()
    succeeding.remove_bg.assert_called_once()


def test_all_providers_fail_reraises_last_error():
    a = MagicMock()
    a.remove_bg.side_effect = RuntimeError("first error")
    b = MagicMock()
    b.remove_bg.side_effect = RuntimeError("last error")
    chain = ChainBgRemovalClient([a, b])
    with pytest.raises(RuntimeError, match="last error"):
        chain.remove_bg(b"img")


# ── rembg client: model config + lock ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_rembg_session():
    """Isolate the module-level session cache between tests."""
    rembg_client._session = None
    rembg_client._session_model = None
    yield
    rembg_client._session = None
    rembg_client._session_model = None


def test_get_session_uses_configured_model(monkeypatch):
    monkeypatch.setattr(rembg_client.settings, "rembg_model", "isnet-general-use")
    fake_session = object()
    with patch("rembg.new_session", return_value=fake_session) as new_session:
        assert rembg_client._get_session() is fake_session
        new_session.assert_called_once_with("isnet-general-use")
        # second call hits the cache — no new session
        assert rembg_client._get_session() is fake_session
        new_session.assert_called_once()


def test_get_session_reloads_on_model_change(monkeypatch):
    monkeypatch.setattr(rembg_client.settings, "rembg_model", "model-a")
    with patch("rembg.new_session", side_effect=[object(), object()]) as new_session:
        first = rembg_client._get_session()
        monkeypatch.setattr(rembg_client.settings, "rembg_model", "model-b")
        second = rembg_client._get_session()
        assert first is not second
        assert new_session.call_count == 2


def test_remove_bg_holds_inference_lock():
    lock_was_held = {}

    def fake_remove(image_bytes, session=None):
        lock_was_held["value"] = rembg_client._inference_lock.locked()
        return b"result-png"

    with patch("rembg.new_session", return_value=object()), \
         patch("rembg.remove", side_effect=fake_remove):
        result = rembg_client.RembgClient().remove_bg(b"img")
    assert result == b"result-png"
    assert lock_was_held["value"] is True
    assert not rembg_client._inference_lock.locked()  # released after call


def test_remove_bg_empty_result_raises():
    with patch("rembg.new_session", return_value=object()), \
         patch("rembg.remove", return_value=b""):
        with pytest.raises(RuntimeError, match="empty result"):
            rembg_client.RembgClient().remove_bg(b"img")


# ── warmup ───────────────────────────────────────────────────────────────────

def test_warmup_loads_session():
    fake_session = object()
    with patch("rembg.new_session", return_value=fake_session):
        rembg_client.warmup()
    assert rembg_client._session is fake_session


def test_warmup_swallows_exceptions():
    with patch("rembg.new_session", side_effect=RuntimeError("download failed")):
        rembg_client.warmup()  # must not raise
    assert rembg_client._session is None
    assert not rembg_client._inference_lock.locked()
