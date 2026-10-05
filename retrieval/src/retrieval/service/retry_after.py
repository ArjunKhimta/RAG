"""How long a caller should wait before asking again, for the `Retry-After` header.

Gemini's free-tier daily limits reset at midnight Pacific time, so a used-up daily quota means
waiting until then. The difference is taken in UTC: subtracting two times in the same time zone
ignores a daylight-saving change between them, which would be an hour out on those two nights.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

QUOTA_RESET_ZONE = ZoneInfo("America/Los_Angeles")

TEMPORARY_FAILURE_RETRY_SECONDS = 30


def seconds_until_daily_quota_reset(now: datetime) -> int:
    local_now = now.astimezone(QUOTA_RESET_ZONE)
    next_midnight = datetime.combine(
        local_now.date() + timedelta(days=1), time(0), tzinfo=QUOTA_RESET_ZONE
    )
    remaining = next_midnight.astimezone(UTC) - now.astimezone(UTC)
    return max(1, math.ceil(remaining.total_seconds()))
