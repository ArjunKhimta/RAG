from __future__ import annotations

from datetime import UTC, datetime

import pytest

from retrieval.quota_clock import quota_day, seconds_until_daily_quota_reset


@pytest.mark.parametrize(
    "now, expected_seconds",
    [
        (datetime(2026, 10, 6, 6, 0, tzinfo=UTC), 3600),
        (datetime(2026, 10, 5, 7, 0, tzinfo=UTC), 24 * 3600),
        (datetime(2026, 10, 6, 6, 59, 59, 500000, tzinfo=UTC), 1),
        (datetime(2026, 1, 15, 9, 0, tzinfo=UTC), 23 * 3600),
        (datetime(2026, 11, 1, 7, 0, tzinfo=UTC), 25 * 3600),
        (datetime(2026, 3, 8, 8, 0, tzinfo=UTC), 23 * 3600),
    ],
)
def test_the_wait_runs_to_the_next_midnight_pacific(now, expected_seconds):
    assert seconds_until_daily_quota_reset(now) == expected_seconds


@pytest.mark.parametrize(
    "now, expected_day",
    [
        (datetime(2026, 10, 6, 6, 59, 59, tzinfo=UTC), "2026-10-05"),
        (datetime(2026, 10, 6, 7, 0, tzinfo=UTC), "2026-10-06"),
        (datetime(2026, 1, 15, 7, 59, 59, tzinfo=UTC), "2026-01-14"),
        (datetime(2026, 1, 15, 8, 0, tzinfo=UTC), "2026-01-15"),
    ],
)
def test_the_quota_day_turns_over_at_midnight_pacific(now, expected_day):
    assert quota_day(now) == expected_day
