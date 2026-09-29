"""Keep API requests and tokens under per-minute limits.

Gemini's free tier counts every text in an embedding batch as one request, so a call carries a
request count as well as a token count: a batch of 100 texts uses 100 requests.

The limiter remembers every call from the last 60 seconds with its request and token counts. A new
call goes ahead only if both totals stay within the limits; otherwise it sleeps until the oldest
remembered call is more than a minute old, then checks again. This sliding window never allows a
burst over the limit, unlike counters that reset on the minute boundary.

The clock and sleep functions are injectable so tests can run without waiting.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        tokens_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._requests_per_minute = requests_per_minute
        self._tokens_per_minute = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._recent_calls: deque[tuple[float, int, int]] = deque()

    def acquire(self, request_count: int, token_count: int) -> float:
        """Wait until a call of this many requests and tokens fits, record it, return the wait."""
        if request_count > self._requests_per_minute:
            raise ValueError(
                f"A call of {request_count} requests can never fit a limit of "
                f"{self._requests_per_minute} requests per minute"
            )
        if token_count > self._tokens_per_minute:
            raise ValueError(
                f"A call of {token_count} tokens can never fit a limit of "
                f"{self._tokens_per_minute} tokens per minute"
            )
        seconds_waited = 0.0
        while True:
            now = self._clock()
            self._forget_calls_before(now - WINDOW_SECONDS)
            if self._has_capacity_for(request_count, token_count):
                self._recent_calls.append((now, request_count, token_count))
                return seconds_waited
            oldest_call_time = self._recent_calls[0][0]
            wait_seconds = oldest_call_time + WINDOW_SECONDS - now
            self._sleep(wait_seconds)
            seconds_waited += wait_seconds

    def _forget_calls_before(self, cutoff_time: float) -> None:
        while self._recent_calls and self._recent_calls[0][0] <= cutoff_time:
            self._recent_calls.popleft()

    def _has_capacity_for(self, request_count: int, token_count: int) -> bool:
        requests_used = sum(requests for _, requests, _ in self._recent_calls)
        tokens_used = sum(tokens for _, _, tokens in self._recent_calls)
        has_request_capacity = requests_used + request_count <= self._requests_per_minute
        has_token_capacity = tokens_used + token_count <= self._tokens_per_minute
        return has_request_capacity and has_token_capacity
