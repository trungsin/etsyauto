"""Etsy public API quota estimation for the idea miner (v0.8.2).

Pure functions, no DB / no I/O — given config + enabled-keyword count, estimate
how many Etsy API calls per day the miner will issue and whether that exceeds
the configured warn threshold.

Formula:
    runs_per_day  = 86400 / etsy_miner_interval_sec
    calls_per_run = 1 (search) + etsy_miner_per_keyword_limit (detail calls)
    daily_calls   = enabled_keywords × runs_per_day × calls_per_run

v0.8 success criterion: "<30% of 10K QPD with 10 keywords on hourly schedule".
Default config (interval=3600, limit=10, 10 keywords) → 10 × 24 × 11 = 2640
calls/day = 26.4% — comfortably under 30%.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class QuotaEstimate:
    """Snapshot of expected Etsy API load given current config + keyword count."""

    enabled_keywords: int
    runs_per_day: int
    calls_per_run: int
    daily_calls: int
    threshold: int

    @property
    def is_over_threshold(self) -> bool:
        """True when projected daily calls exceed the warn threshold."""
        return self.daily_calls > self.threshold

    @property
    def percent_of_etsy_cap(self) -> float:
        """Daily calls as a percentage of Etsy's 10K-per-day public cap."""
        return round(self.daily_calls / 10000.0 * 100.0, 1)


def estimate(enabled_keywords: int) -> QuotaEstimate:
    """Estimate daily Etsy API load for the given enabled-keyword count.

    Reads settings.etsy_miner_interval_sec, etsy_miner_per_keyword_limit,
    and etsy_quota_warn_threshold_calls_per_day at call time so .env changes
    take effect without restarting the module.
    """
    interval = max(settings.etsy_miner_interval_sec, 1)
    limit = max(settings.etsy_miner_per_keyword_limit, 0)
    threshold = settings.etsy_quota_warn_threshold_calls_per_day

    runs_per_day = 86400 // interval
    calls_per_run = 1 + limit  # 1 search + N detail calls
    daily_calls = max(enabled_keywords, 0) * runs_per_day * calls_per_run

    return QuotaEstimate(
        enabled_keywords=enabled_keywords,
        runs_per_day=runs_per_day,
        calls_per_run=calls_per_run,
        daily_calls=daily_calls,
        threshold=threshold,
    )
