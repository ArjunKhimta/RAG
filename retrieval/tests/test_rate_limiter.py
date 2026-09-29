from __future__ import annotations

import pytest

from retrieval.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _limiter(clock: FakeClock, requests_per_minute: int, tokens_per_minute: int) -> RateLimiter:
    return RateLimiter(requests_per_minute, tokens_per_minute, clock=clock.time, sleep=clock.sleep)


def test_calls_within_the_limits_go_ahead_without_waiting():
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_minute=3, tokens_per_minute=100)

    waits = [limiter.acquire(request_count=1, token_count=10) for _ in range(3)]

    assert waits == [0.0, 0.0, 0.0]
    assert clock.sleeps == []


def test_a_call_over_the_request_limit_waits_until_the_oldest_is_a_minute_old():
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_minute=2, tokens_per_minute=1000)
    limiter.acquire(request_count=1, token_count=1)
    clock.now += 10
    limiter.acquire(request_count=1, token_count=1)
    clock.now += 5

    waited = limiter.acquire(request_count=1, token_count=1)

    assert waited == pytest.approx(45.0)
    assert clock.now == pytest.approx(1060.0)


def test_every_text_in_a_batch_counts_as_a_request():
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_minute=100, tokens_per_minute=1_000_000)
    limiter.acquire(request_count=100, token_count=1)

    waited = limiter.acquire(request_count=1, token_count=1)

    assert waited == pytest.approx(60.0)


def test_a_call_over_the_token_limit_waits_even_with_requests_to_spare():
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_minute=100, tokens_per_minute=100)
    limiter.acquire(request_count=1, token_count=80)

    waited = limiter.acquire(request_count=1, token_count=30)

    assert waited == pytest.approx(60.0)


def test_the_window_slides_so_old_calls_stop_counting():
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_minute=1, tokens_per_minute=1000)
    limiter.acquire(request_count=1, token_count=1)
    clock.now += 61

    assert limiter.acquire(request_count=1, token_count=1) == 0.0


def test_a_call_larger_than_either_limit_is_refused():
    limiter = _limiter(FakeClock(), requests_per_minute=10, tokens_per_minute=100)

    with pytest.raises(ValueError):
        limiter.acquire(request_count=1, token_count=101)
    with pytest.raises(ValueError):
        limiter.acquire(request_count=11, token_count=1)
