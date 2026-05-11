"""Tests for etsy_quota.estimate (v0.8.2).

Covers:
- Default config produces ≤30% of 10K Etsy QPD with 10 enabled keywords
  (= the v0.8 success criterion that was previously paper-claimed)
- Over-threshold detection works
- Edge cases: 0 keywords, custom intervals
"""
from __future__ import annotations

from app.config import settings
from app.services import etsy_quota


def test_default_config_meets_v08_quota_spec(monkeypatch):
    """Default v0.8.2 config × 10 enabled keywords stays under 30% Etsy 10K cap."""
    # Use the v0.8.2 baked-in defaults (don't depend on .env in CI)
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 3600)
    monkeypatch.setattr(settings, "etsy_miner_per_keyword_limit", 10)

    est = etsy_quota.estimate(enabled_keywords=10)

    assert est.runs_per_day == 24
    assert est.calls_per_run == 11  # 1 search + 10 detail
    assert est.daily_calls == 2640  # 10 × 24 × 11
    assert est.percent_of_etsy_cap == 26.4  # < 30%
    assert est.is_over_threshold is False


def test_legacy_v080_config_would_have_tripped_threshold(monkeypatch):
    """Pre-v0.8.2 default (limit=100) would have produced 24,240 calls/day = 242%."""
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 3600)
    monkeypatch.setattr(settings, "etsy_miner_per_keyword_limit", 100)

    est = etsy_quota.estimate(enabled_keywords=10)

    assert est.daily_calls == 24240  # 10 × 24 × 101
    assert est.percent_of_etsy_cap > 200.0
    assert est.is_over_threshold is True


def test_zero_keywords_produces_zero_calls(monkeypatch):
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 3600)
    monkeypatch.setattr(settings, "etsy_miner_per_keyword_limit", 10)

    est = etsy_quota.estimate(enabled_keywords=0)

    assert est.daily_calls == 0
    assert est.is_over_threshold is False


def test_threshold_breach_above_30_percent(monkeypatch):
    """100 enabled keywords with default v0.8.2 limit still trips the warn banner."""
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 3600)
    monkeypatch.setattr(settings, "etsy_miner_per_keyword_limit", 10)
    monkeypatch.setattr(settings, "etsy_quota_warn_threshold_calls_per_day", 3000)

    est = etsy_quota.estimate(enabled_keywords=100)

    assert est.daily_calls == 26400  # 100 × 24 × 11
    assert est.is_over_threshold is True


def test_custom_interval_changes_estimate(monkeypatch):
    """Halving interval doubles daily calls."""
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 1800)  # 30min
    monkeypatch.setattr(settings, "etsy_miner_per_keyword_limit", 10)

    est = etsy_quota.estimate(enabled_keywords=10)

    assert est.runs_per_day == 48
    assert est.daily_calls == 10 * 48 * 11
    assert est.is_over_threshold is True  # 5280 > 3000


def test_negative_inputs_clamped(monkeypatch):
    """Negative limits or keywords don't produce negative daily calls."""
    monkeypatch.setattr(settings, "etsy_miner_interval_sec", 3600)
    monkeypatch.setattr(settings, "etsy_miner_per_keyword_limit", -5)

    est = etsy_quota.estimate(enabled_keywords=-3)

    assert est.daily_calls == 0
