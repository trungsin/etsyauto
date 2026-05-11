"""Tests for NOTION_SYNC_ENABLED scheduler gating (v0.8.1 hotfix).

Covers:
- sync_to_notion + pull_approvals registered when notion_sync_enabled=True
- Both jobs NOT registered when notion_sync_enabled=False (default)
- Non-notion jobs (title_optimizer, mockup_pipeline, etsy_uploader) always register
- /health endpoint surfaces notion_sync_enabled flag
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_job_registrations(monkeypatch, notion_sync_enabled: bool) -> list[dict]:
    """Run _register_jobs() with mocked workers and collect add_job calls."""
    monkeypatch.setattr(settings, "notion_sync_enabled", notion_sync_enabled)
    monkeypatch.setattr(settings, "idea_mining_enabled", False)  # isolate scope

    with patch("app.workers.title_optimizer.run_title_optimizer_job", create=True), \
         patch("app.workers.mockup_pipeline.run_mockup_pipeline_job", create=True), \
         patch("app.workers.notion_sync.sync_to_notion", create=True), \
         patch("app.workers.notion_sync.pull_approvals", create=True), \
         patch("app.workers.etsy_uploader.run_etsy_uploader_job", create=True):

        from app.scheduler import scheduler, _register_jobs

        recorded: list[dict] = []

        def _capture_add_job(func, **kwargs):
            recorded.append({"id": kwargs.get("id"), "kwargs": kwargs})
            return MagicMock()

        with patch.object(scheduler, "add_job", side_effect=_capture_add_job):
            _register_jobs()

        return recorded


# ---------------------------------------------------------------------------
# Tests — scheduler gating
# ---------------------------------------------------------------------------


def test_notion_jobs_registered_when_enabled(monkeypatch):
    """When NOTION_SYNC_ENABLED=true, both sync_to_notion + pull_approvals register."""
    registrations = _collect_job_registrations(monkeypatch, notion_sync_enabled=True)
    job_ids = [r["id"] for r in registrations]
    assert "sync_to_notion" in job_ids
    assert "pull_approvals" in job_ids


def test_notion_jobs_not_registered_when_disabled(monkeypatch):
    """Default NOTION_SYNC_ENABLED=false → neither Notion job registered."""
    registrations = _collect_job_registrations(monkeypatch, notion_sync_enabled=False)
    job_ids = [r["id"] for r in registrations]
    assert "sync_to_notion" not in job_ids
    assert "pull_approvals" not in job_ids


def test_non_notion_jobs_always_register(monkeypatch):
    """Title optimizer + mockup pipeline + etsy uploader always register regardless of flag."""
    for flag in (True, False):
        registrations = _collect_job_registrations(monkeypatch, notion_sync_enabled=flag)
        job_ids = [r["id"] for r in registrations]
        assert "title_optimizer" in job_ids
        assert "mockup_pipeline" in job_ids
        assert "etsy_uploader" in job_ids


def test_notion_jobs_have_correct_settings(monkeypatch):
    """When enabled, both jobs have coalesce=True, max_instances=1, replace_existing=True."""
    registrations = _collect_job_registrations(monkeypatch, notion_sync_enabled=True)
    for job_id in ("sync_to_notion", "pull_approvals"):
        reg = next(r for r in registrations if r["id"] == job_id)
        kw = reg["kwargs"]
        assert kw.get("coalesce") is True
        assert kw.get("max_instances") == 1
        assert kw.get("replace_existing") is True
        assert kw.get("trigger") == "interval"


# ---------------------------------------------------------------------------
# Tests — /health flag exposure
# ---------------------------------------------------------------------------


def test_health_endpoint_exposes_notion_flag(monkeypatch):
    """GET /health includes notion_sync_enabled in JSON body."""
    monkeypatch.setattr(settings, "notion_sync_enabled", False)
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert "notion_sync_enabled" in body
        assert body["notion_sync_enabled"] is False


def test_health_reflects_enabled_flag(monkeypatch):
    """When NOTION_SYNC_ENABLED=true, /health body reflects True."""
    monkeypatch.setattr(settings, "notion_sync_enabled", True)
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/health")
        body = resp.json()
        assert body["notion_sync_enabled"] is True
