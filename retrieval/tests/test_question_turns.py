from __future__ import annotations

import threading
import time

import pytest

from retrieval.service.question_turns import QuestionsBusyError, QuestionTurns


class Answering:
    """Holds a turn until told to finish, so a test can see who waits and who is refused."""

    def __init__(self, turns: QuestionTurns) -> None:
        self.started = threading.Event()
        self.finish = threading.Event()
        self.outcome: str | None = None
        self._thread = threading.Thread(target=self._run, args=(turns,))

    def start(self) -> Answering:
        self._thread.start()
        return self

    def join(self) -> None:
        self._thread.join(timeout=5)

    def _run(self, turns: QuestionTurns) -> None:
        try:
            with turns.turn():
                self.started.set()
                self.finish.wait(timeout=5)
            self.outcome = "answered"
        except QuestionsBusyError:
            self.outcome = "refused"


def _wait_for_waiting(turns: QuestionTurns, count: int) -> None:
    deadline = time.monotonic() + 2
    while turns._admitted < 1 + count and time.monotonic() < deadline:
        time.sleep(0.005)
    assert turns._admitted == 1 + count


def test_questions_are_answered_one_at_a_time():
    turns = QuestionTurns(max_waiting=5, max_wait_seconds=5)
    active = 0
    most_active = 0
    counter_lock = threading.Lock()

    def answer_once():
        nonlocal active, most_active
        with turns.turn():
            with counter_lock:
                active += 1
                most_active = max(most_active, active)
            time.sleep(0.02)
            with counter_lock:
                active -= 1

    threads = [threading.Thread(target=answer_once) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert most_active == 1


def test_one_answering_and_two_waiting_are_admitted_and_a_fourth_is_refused_at_once():
    turns = QuestionTurns(max_waiting=2, max_wait_seconds=5)
    first = Answering(turns).start()
    assert first.started.wait(timeout=2)
    waiting = [Answering(turns).start() for _ in range(2)]
    _wait_for_waiting(turns, 2)

    refused_at = time.monotonic()
    with pytest.raises(QuestionsBusyError, match="2 questions already waiting"):
        with turns.turn():
            pass
    refusal_seconds = time.monotonic() - refused_at

    first.finish.set()
    for question in waiting:
        question.finish.set()
    for question in [first, *waiting]:
        question.join()
    assert refusal_seconds < 0.5
    assert [question.outcome for question in [first, *waiting]] == ["answered"] * 3


def test_a_waiting_question_gives_up_when_its_turn_does_not_come_in_time():
    turns = QuestionTurns(max_waiting=2, max_wait_seconds=0.1)
    slow = Answering(turns).start()
    assert slow.started.wait(timeout=2)

    waited_from = time.monotonic()
    with pytest.raises(QuestionsBusyError, match="No turn within 0.1 s"):
        with turns.turn():
            pass
    waited_seconds = time.monotonic() - waited_from

    slow.finish.set()
    slow.join()
    assert 0.09 <= waited_seconds < 1


def test_refused_and_timed_out_questions_give_their_place_back():
    turns = QuestionTurns(max_waiting=0, max_wait_seconds=0.05)
    slow = Answering(turns).start()
    assert slow.started.wait(timeout=2)
    with pytest.raises(QuestionsBusyError):
        with turns.turn():
            pass
    slow.finish.set()
    slow.join()

    with turns.turn():
        assert turns._admitted == 1
    assert turns._admitted == 0


def test_a_failed_answer_releases_the_turn():
    turns = QuestionTurns(max_waiting=0, max_wait_seconds=0.05)

    with pytest.raises(RuntimeError):
        with turns.turn():
            raise RuntimeError("the answer failed")

    with turns.turn():
        pass
    assert turns._admitted == 0
