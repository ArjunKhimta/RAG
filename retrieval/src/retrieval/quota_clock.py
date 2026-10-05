"""Gemini's quota day: which day a moment counts toward, and how long until the next one starts.

Gemini's free-tier daily limits reset at midnight Pacific time, so a quota day runs from one
Pacific midnight to the next, and a used-up daily quota means waiting until the next one. Time
differences are taken in UTC: subtracting two times in the same time zone ignores a daylight-saving
change between them, which would be an hour out on those two nights.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

QUOTA_RESET_ZONE = ZoneInfo("America/Los_Angeles")


def quota_day(now: datetime) -> str:
    """The quota day `now` counts toward, as an ISO date such as 2026-10-05."""
    return now.astimezone(QUOTA_RESET_ZONE).date().isoformat()


def seconds_until_daily_quota_reset(now: datetime) -> int:
    local_now = now.astimezone(QUOTA_RESET_ZONE)
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time(0), tzinfo=QUOTA_RESET_ZONE
    )
    remaining = next_midnight.astimezone(UTC) - now.astimezone(UTC)
    return max(1, math.ceil(remaining.total_seconds()))
