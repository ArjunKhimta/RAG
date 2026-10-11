"""Questions take turns: one is answered at a time, a few may wait, and none waits long.

Answering stays one at a time, as it was before searches stopped waiting with it: an answer takes
about 2 seconds, so one line already handles about 30 a minute against Gemini's 15, and when
Gemini slows down, answering several at once would only send more calls to a service that is
already struggling.

Two rules keep a slow answer from turning into a long queue:
- at most `max_waiting` questions wait for their turn; one more is refused at once
- a waiting question gives up after `max_wait_seconds` without its turn

Both refusals raise `QuestionsBusyError`, which the service turns into 503 with `Retry-After`, so
the caller hears within seconds rather than waiting toward its own time limit. The rules also
bound how many of the server's threads questions can hold (one answering plus `max_waiting`),
leaving the rest for searches, job status polls, and health checks.

Turns are not strictly first come, first served: Python's lock lets whichever waiting thread
wakes first go next. With at most two waiting, the difference is a few seconds at most.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager


class QuestionsBusyError(Exception):
    """Raised when a question cannot get its turn: the queue is full, or the wait ran out."""


class QuestionTurns:
    def __init__(self, max_waiting: int, max_wait_seconds: float) -> None:
        self._max_admitted = 1 + max_waiting
        self._max_wait_seconds = max_wait_seconds
        self._turn = threading.Lock()
        self._admitted = 0
        self._admitted_lock = threading.Lock()

    @contextmanager
    def turn(self) -> Iterator[None]:
        """Wait for this question's turn and hold it inside the `with` block."""
        self._admit()
        try:
            if not self._turn.acquire(timeout=self._max_wait_seconds):
                raise QuestionsBusyError(
                    f"No turn within {self._max_wait_seconds:g} s; another answer is slow"
                )
            try:
                yield
            finally:
                self._turn.release()
        finally:
            self._leave()

    def _admit(self) -> None:
        with self._admitted_lock:
            if self._admitted >= self._max_admitted:
                raise QuestionsBusyError(
                    f"{self._admitted - 1} questions already waiting, the most allowed"
                )
            self._admitted += 1

    def _leave(self) -> None:
        with self._admitted_lock:
            self._admitted -= 1
