"""Tests for idea_mining_job scheduler registration.

Covers:
- idea_mining_job is registered when idea_mining_enabled=True
- idea_mining_job is NOT registered when idea_mining_enabled=False
- Registered job has correct coalesce=True and max_instances=1 settings
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _collect_job_registrations(monkeypatch, idea_mining_enabled: bool) -> list[dict]:
    """Run _register_jobs() with mocked workers and collect add_job calls."""
    monkeypatch.setattr(settings, "idea_mining_enabled", idea_mining_enabled)

    # Patch all worker imports so they don't need real DB / external services
    with patch("app.workers.title_optimizer.run_title_optimizer_job", create=True), \
         patch("app.workers.mockup_pipeline.run_mockup_pipeline_job", create=True), \
         patch("app.workers.notion_sync.sync_to_notion", create=True), \
         patch("app.workers.notion_sync.pull_approvals", create=True), \
         patch("app.workers.etsy_uploader.run_etsy_uploader_job", create=True), \
         patch("app.services.idea_miner_service.run_all", create=True):

        from app.scheduler import scheduler, _register_jobs

        recorded: list[dict] = []

        original_add_job = scheduler.add_job

        def _capture_add_job(func, **kwargs):
            recorded.append({"id": kwargs.get("id"), "kwargs": kwargs})
            # Don't actually add job to live scheduler — just record
            return MagicMock()

        with patch.object(scheduler, "add_job", side_effect=_capture_add_job):
            _register_jobs()

        return recorded


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_idea_mining_job_registered_when_enabled(monkeypatch):
    """When idea_mining_enabled=True, idea_mining_job appears in registered jobs."""
    registrations = _collect_job_registrations(monkeypatch, idea_mining_enabled=True)
    job_ids = [r["id"] for r in registrations]
    assert "idea_mining_job" in job_ids


def test_idea_mining_job_not_registered_when_disabled(monkeypatch):
    """When idea_mining_enabled=False, idea_mining_job is NOT registered."""
    registrations = _collect_job_registrations(monkeypatch, idea_mining_enabled=False)
    job_ids = [r["id"] for r in registrations]
    assert "idea_mining_job" not in job_ids


def test_idea_mining_job_has_correct_settings(monkeypatch):
    """idea_mining_job is registered with coalesce=True and max_instances=1."""
    registrations = _collect_job_registrations(monkeypatch, idea_mining_enabled=True)
    mining_reg = next(r for r in registrations if r["id"] == "idea_mining_job")
    kw = mining_reg["kwargs"]
    assert kw.get("coalesce") is True
    assert kw.get("max_instances") == 1
    assert kw.get("replace_existing") is True
    assert kw.get("trigger") == "interval"


def test_idea_mining_job_interval_matches_settings(monkeypatch):
    """idea_mining_job interval matches settings.etsy_miner_interval_sec."""
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 7200)
    registrations = _collect_job_registrations(monkeypatch, idea_mining_enabled=True)
    mining_reg = next(r for r in registrations if r["id"] == "idea_mining_job")
    assert mining_reg["kwargs"].get("seconds") == 7200
