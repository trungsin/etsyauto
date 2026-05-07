"""Tests for listing_pre_check — fail-fast Etsy hard-cap validation."""
from __future__ import annotations

import pytest

from app.services.listing_pre_check import (
    Issue,
    PreCheckFailed,
    pre_check_listing,
)


def test_no_issues_when_all_within_caps():
    issues = pre_check_listing(
        title="t" * 50,
        tags=["a", "b", "c"],
        enabled_combos=[{"size": "S", "color": "White"}],
        composite_size=(800, 800),
    )
    assert issues == []


def test_title_over_140_chars_flagged():
    issues = pre_check_listing(
        title="x" * 141,
        tags=[],
        enabled_combos=[{"size": "S", "color": "White"}],
    )
    assert any(i.field == "title" for i in issues)


def test_more_than_13_tags_flagged():
    issues = pre_check_listing(
        title="ok",
        tags=[f"t{i}" for i in range(14)],
        enabled_combos=[{"size": "S", "color": "W"}],
    )
    assert any(i.field == "tags" for i in issues)


def test_more_than_30_combos_flagged():
    issues = pre_check_listing(
        title="ok",
        tags=[],
        enabled_combos=[{"size": f"S{i}", "color": "W"} for i in range(31)],
    )
    assert any(i.field == "enabled_combos" for i in issues)


def test_composite_below_570_flagged():
    issues = pre_check_listing(
        title="ok",
        tags=[],
        enabled_combos=[{"size": "S", "color": "W"}],
        composite_size=(400, 800),
    )
    assert any(i.field == "composite" for i in issues)


def test_pre_check_failed_carries_all_issues():
    issues = [Issue("title", "too long")]
    exc = PreCheckFailed(issues)
    assert exc.issues == issues
    assert "too long" in str(exc)


def test_pre_check_failed_is_a_value_error():
    """Routes catch ValueError as fallback; PreCheckFailed must be one."""
    assert issubclass(PreCheckFailed, ValueError)
