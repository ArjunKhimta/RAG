from __future__ import annotations

import sys
import threading

import pytest

from retrieval.rate_limiter import RateLimiter, RateLimitWaitTooLongError


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


def test_a_call_that_needs_several_old_calls_to_leave_waits_once_for_all_of_them():
    clock = FakeClock()
    limiter = _limiter(clock, requests_per_minute=3, tokens_per_minute=1000)
    for _ in range(3):
        limiter.acquire(request_count=1, token_count=1)
        clock.now += 10

    waited = limiter.acquire(request_count=2, token_count=1)

    assert waited == pytest.approx(40.0)
    assert clock.sleeps == [pytest.approx(40.0)]


def test_a_limiter_with_a_maximum_wait_refuses_a_longer_wait_with_its_length():
    clock = FakeClock()
    limiter = RateLimiter(1, 1000, clock=clock.time, sleep=clock.sleep, max_wait_seconds=5)
    limiter.acquire(request_count=1, token_count=1)
    clock.now += 48

    with pytest.raises(RateLimitWaitTooLongError) as refused:
        limiter.acquire(request_count=1, token_count=1)

    assert refused.value.wait_seconds == pytest.approx(12.0)
    assert clock.sleeps == []


def test_a_limiter_with_a_maximum_wait_still_waits_within_it():
    clock = FakeClock()
    limiter = RateLimiter(1, 1000, clock=clock.time, sleep=clock.sleep, max_wait_seconds=5)
    limiter.acquire(request_count=1, token_count=1)
    clock.now += 57

    waited = limiter.acquire(request_count=1, token_count=1)

    assert waited == pytest.approx(3.0)


def test_a_refused_call_takes_no_slot():
    clock = FakeClock()
    limiter = RateLimiter(1, 1000, clock=clock.time, sleep=clock.sleep, max_wait_seconds=0)
    limiter.acquire(request_count=1, token_count=1)
    with pytest.raises(RateLimitWaitTooLongError):
        limiter.acquire(request_count=1, token_count=1)
    clock.now += 60

    assert limiter.acquire(request_count=1, token_count=1) == 0.0


@pytest.fixture
def threads_switch_constantly():
    """Make Python switch threads every microsecond instead of every 5 ms, so a thread is likely
    to be interrupted between checking for room and recording its call, where a missing lock
    would let a second thread take the same slot."""
    usual_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(usual_interval)


def _outcomes_of_threads_at_once(thread_count: int, slot_count: int) -> list[str]:
    """Each thread's outcome: a slot, a refusal, or any other error (a corrupted window)."""
    limiter = RateLimiter(slot_count, 1_000_000, max_wait_seconds=0)
    outcomes: list[str] = []
    start_together = threading.Barrier(thread_count)

    def take_a_slot():
        start_together.wait()
        try:
            limiter.acquire(request_count=1, token_count=1)
            outcomes.append("slot")
        except RateLimitWaitTooLongError:
            outcomes.append("refused")
        except Exception as error:
            outcomes.append(type(error).__name__)

    threads = [threading.Thread(target=take_a_slot) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return sorted(outcomes)


def test_threads_sharing_a_limiter_never_take_more_slots_than_it_has(threads_switch_constantly):
    """A race shows up only by chance, so the same rush of 40 threads is tried 20 times."""
    expected = ["refused"] * 30 + ["slot"] * 10

    for _ in range(20):
        assert _outcomes_of_threads_at_once(40, 10) == expected


def test_a_sleeping_thread_does_not_hold_up_another():
    clock = FakeClock()
    clock_lock = threading.Lock()
    sleepers: list[str] = []
    both_sleeping = threading.Barrier(2, timeout=2)

    def sleep(seconds):
        sleepers.append(threading.current_thread().name)
        if len(sleepers) <= 2:
            both_sleeping.wait()
        with clock_lock:
            clock.now += seconds

    limiter = RateLimiter(1, 1000, clock=clock.time, sleep=sleep)
    limiter.acquire(request_count=1, token_count=1)
    threads = [
        threading.Thread(target=limiter.acquire, args=(1, 1), name=name)
        for name in ("first", "second")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(sleepers[:2]) == ["first", "second"]
    assert not any(thread.is_alive() for thread in threads)
