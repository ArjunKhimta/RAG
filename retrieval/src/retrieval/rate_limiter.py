"""Keep API requests and tokens under per-minute limits.

Gemini's free tier counts every text in an embedding batch as one request, so a call carries a
request count as well as a token count: a batch of 100 texts uses 100 requests.

The limiter remembers every call from the last 60 seconds with its request and token counts. A new
call goes ahead only if both totals stay within the limits; otherwise it sleeps until enough of the
remembered calls are more than a minute old, then checks again. This sliding window never allows a
burst over the limit, unlike counters that reset on the minute boundary.

One limiter may be shared by several threads (the service answers searches while a question is
being answered). Checking for room and recording the call happen together under a lock, so two
threads can never both take the last slot; the lock is released while a thread sleeps, so one
waiting thread does not hold up the others.

A limiter made with `max_wait_seconds` refuses instead of waiting longer than that: it raises
`RateLimitWaitTooLongError` carrying the exact wait, so the service can answer at once with a
`Retry-After` rather than keep a request waiting toward its caller's time limit. Scripts make
limiters without it and wait as long as it takes.

The clock and sleep functions are injectable so tests can run without waiting.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS = 60.0


class RateLimitWaitTooLongError(Exception):
    """Raised when a call would have to wait longer than the limiter's `max_wait_seconds`."""

    def __init__(self, wait_seconds: float, max_wait_seconds: float) -> None:
        super().__init__(
            f"The per-minute limit would need a wait of {wait_seconds:.1f} s, "
            f"more than the {max_wait_seconds:g} s allowed"
        )
        self.wait_seconds = wait_seconds


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        tokens_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_wait_seconds: float | None = None,
    ) -> None:
        self._requests_per_minute = requests_per_minute
        self._tokens_per_minute = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._max_wait_seconds = max_wait_seconds
        self._recent_calls: deque[tuple[float, int, int]] = deque()
        self._lock = threading.Lock()

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
            with self._lock:
                now = self._clock()
                self._forget_calls_before(now - WINDOW_SECONDS)
                wait_seconds = self._seconds_until_room_for(request_count, token_count, now)
                if wait_seconds <= 0:
                    self._recent_calls.append((now, request_count, token_count))
                    return seconds_waited
            self._refuse_if_too_long(seconds_waited + wait_seconds)
            self._sleep(wait_seconds)
            seconds_waited += wait_seconds

    def _forget_calls_before(self, cutoff_time: float) -> None:
        while self._recent_calls and self._recent_calls[0][0] <= cutoff_time:
            self._recent_calls.popleft()

    def _seconds_until_room_for(self, request_count: int, token_count: int, now: float) -> float:
        """How long until enough remembered calls leave the window for this call to fit; 0 if
        it fits now. Calls leave in the order they were made, oldest first."""
        requests_used = sum(requests for _, requests, _ in self._recent_calls)
        tokens_used = sum(tokens for _, _, tokens in self._recent_calls)
        if self._fits(requests_used, tokens_used, request_count, token_count):
            return 0.0
        for call_time, call_requests, call_tokens in self._recent_calls:
            requests_used -= call_requests
            tokens_used -= call_tokens
            if self._fits(requests_used, tokens_used, request_count, token_count):
                return call_time + WINDOW_SECONDS - now
        raise AssertionError("unreachable: an empty window always has room for a call that fits")

    def _fits(
        self, requests_used: int, tokens_used: int, request_count: int, token_count: int
    ) -> bool:
        has_request_capacity = requests_used + request_count <= self._requests_per_minute
        has_token_capacity = tokens_used + token_count <= self._tokens_per_minute
        return has_request_capacity and has_token_capacity

    def _refuse_if_too_long(self, total_wait_seconds: float) -> None:
        if self._max_wait_seconds is None:
            return
        if total_wait_seconds > self._max_wait_seconds:
            raise RateLimitWaitTooLongError(total_wait_seconds, self._max_wait_seconds)
