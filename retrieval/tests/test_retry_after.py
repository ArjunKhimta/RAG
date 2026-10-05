from __future__ import annotations

from datetime import UTC, datetime

import pytest

from retrieval.service.retry_after import seconds_until_daily_quota_reset


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
