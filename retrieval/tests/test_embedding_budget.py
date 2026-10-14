"""Tests for the embedding lanes, with a small stand-in for the usage collection that applies the
two updates the budget makes, so nothing reaches MongoDB or Gemini. Atomicity under concurrent
callers is checked against real Atlas in the integration tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from retrieval.embedding_budget import (
    EMBEDDING_USAGE_COLLECTION,
    EmbeddingBudgetExhaustedError,
    EmbeddingLane,
    MeteredDocumentEmbedder,
    MeteredQueryEmbedder,
    MongoEmbeddingBudget,
)
from retrieval.embedding_inputs import estimate_tokens
from retrieval.query_cache import CachingQueryEmbedder
from retrieval.rate_limiter import RateLimitWaitTooLongError

NOON_PACIFIC = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

NEXT_DAY_PACIFIC = datetime(2026, 10, 6, 7, 30, tzinfo=UTC)


@dataclass(frozen=True)
class UpdateResult:
    matched_count: int


class FakeUsageCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.indexes: list[tuple] = []

    def create_index(self, keys, expireAfterSeconds):
        self.indexes.append((keys, expireAfterSeconds))

    def update_one(self, conditions, update, upsert=False) -> UpdateResult:
        document = self.documents.get(conditions["_id"])
        if document is None:
            if upsert:
                self.documents[conditions["_id"]] = {
                    "_id": conditions["_id"],
                    **update.get("$setOnInsert", {}),
                }
            return UpdateResult(matched_count=0)
        for field_name, condition in conditions.items():
            if field_name != "_id" and document[field_name] > condition["$lte"]:
                return UpdateResult(matched_count=0)
        for field_name, amount in update.get("$inc", {}).items():
            document[field_name] += amount
        return UpdateResult(matched_count=1)

    def find_one(self, query):
        return self.documents.get(query["_id"])


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _budget(clock: Clock | None = None, collection: FakeUsageCollection | None = None):
    usage_collection = collection or FakeUsageCollection()
    database = {EMBEDDING_USAGE_COLLECTION: usage_collection}
    return MongoEmbeddingBudget(database, clock=clock or Clock(NOON_PACIFIC)), usage_collection


def test_indexing_may_use_eight_hundred_a_day_and_a_refusal_books_nothing():
    budget, _ = _budget()
    for _ in range(10):
        budget.reserve(EmbeddingLane.INDEXING, 80)

    with pytest.raises(EmbeddingBudgetExhaustedError, match="at most 800 of the 1000"):
        budget.reserve(EmbeddingLane.INDEXING, 1)

    usage = budget.usage_today()
    assert (usage.indexing, usage.questions, usage.total) == (800, 0, 800)


def test_questions_keep_the_last_two_hundred_after_indexing_uses_its_lane():
    budget, _ = _budget()
    for _ in range(10):
        budget.reserve(EmbeddingLane.INDEXING, 80)

    for _ in range(200):
        budget.reserve(EmbeddingLane.QUESTIONS, 1)

    with pytest.raises(EmbeddingBudgetExhaustedError, match="budget of 1000 is used up"):
        budget.reserve(EmbeddingLane.QUESTIONS, 1)
    assert budget.usage_today().total == 1000


def test_questions_may_use_seats_indexing_left_and_indexing_then_gets_less():
    budget, _ = _budget()
    for _ in range(300):
        budget.reserve(EmbeddingLane.QUESTIONS, 1)

    budget.reserve(EmbeddingLane.INDEXING, 80 * 8)
    budget.reserve(EmbeddingLane.INDEXING, 60)

    with pytest.raises(EmbeddingBudgetExhaustedError):
        budget.reserve(EmbeddingLane.INDEXING, 1)
    usage = budget.usage_today()
    assert (usage.indexing, usage.questions, usage.total) == (700, 300, 1000)


def test_a_batch_is_booked_whole_or_not_at_all():
    budget, _ = _budget()
    budget.reserve(EmbeddingLane.INDEXING, 760)

    with pytest.raises(EmbeddingBudgetExhaustedError):
        budget.reserve(EmbeddingLane.INDEXING, 80)

    budget.reserve(EmbeddingLane.INDEXING, 40)
    assert budget.usage_today().indexing == 800


def test_a_refusal_says_how_many_seats_the_lane_has_left():
    budget, _ = _budget()
    budget.reserve(EmbeddingLane.INDEXING, 760)

    with pytest.raises(EmbeddingBudgetExhaustedError) as refusal:
        budget.reserve(EmbeddingLane.INDEXING, 80)

    assert refusal.value.seats_left == 40


def test_indexing_seats_left_are_capped_by_what_questions_left_of_the_day():
    budget, _ = _budget()
    budget.reserve(EmbeddingLane.INDEXING, 700)
    budget.reserve(EmbeddingLane.QUESTIONS, 290)

    with pytest.raises(EmbeddingBudgetExhaustedError) as indexing_refusal:
        budget.reserve(EmbeddingLane.INDEXING, 80)
    budget.reserve(EmbeddingLane.QUESTIONS, 10)
    with pytest.raises(EmbeddingBudgetExhaustedError) as question_refusal:
        budget.reserve(EmbeddingLane.QUESTIONS, 1)

    assert indexing_refusal.value.seats_left == 10
    assert question_refusal.value.seats_left == 0


def test_a_new_quota_day_starts_empty_at_midnight_pacific():
    clock = Clock(NOON_PACIFIC)
    budget, collection = _budget(clock)
    budget.reserve(EmbeddingLane.INDEXING, 800)

    clock.now = NEXT_DAY_PACIFIC
    budget.reserve(EmbeddingLane.INDEXING, 80)

    assert budget.usage_today().day == "2026-10-06"
    assert budget.usage_today().indexing == 80
    assert collection.documents["2026-10-05"]["indexing"] == 800
    assert collection.documents["2026-10-06"]["created_at"] == NEXT_DAY_PACIFIC


@pytest.mark.parametrize(
    "lane, count",
    [(EmbeddingLane.INDEXING, 801), (EmbeddingLane.QUESTIONS, 1001), (EmbeddingLane.QUESTIONS, 0)],
)
def test_a_call_that_could_never_fit_is_a_programming_error(lane, count):
    budget, collection = _budget()

    with pytest.raises(ValueError, match="can never fit"):
        budget.reserve(lane, count)

    assert collection.documents == {}


def test_usage_is_zero_before_anything_is_booked():
    budget, _ = _budget()

    usage = budget.usage_today()

    assert (usage.day, usage.indexing, usage.questions, usage.total) == ("2026-10-05", 0, 0, 0)


def test_usage_documents_expire_after_thirty_days():
    budget, collection = _budget()

    budget.ensure_indexes()

    assert collection.indexes == [([("created_at", 1)], 30 * 24 * 60 * 60)]


def test_a_used_up_budget_counts_as_a_used_up_daily_quota_and_is_never_retried():
    error = EmbeddingBudgetExhaustedError("used up")

    assert error.is_daily_quota_exhausted is True
    assert error.is_retryable is False


class RecordingBudget:
    def __init__(self, refuse: bool = False) -> None:
        self.refuse = refuse
        self.reservations: list[tuple[EmbeddingLane, int]] = []

    def reserve(self, lane: EmbeddingLane, count: int) -> None:
        if self.refuse:
            raise EmbeddingBudgetExhaustedError("used up")
        self.reservations.append((lane, count))


class FakeEmbedder:
    def __init__(self) -> None:
        self.model_id = "fake-model"
        self.dimensions = 2
        self.task_type = "TASK"
        self.calls: list = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    def count_tokens(self, text: str) -> int:
        return 7

    def count_batch_tokens(self, texts: list[str]) -> int:
        return sum(estimate_tokens(text) for text in texts)

    def embed_query(self, question: str) -> list[float]:
        self.calls.append(question)
        return [1.0, 0.0]


class RecordingRateLimiter:
    def __init__(self) -> None:
        self.acquired: list[tuple[int, int]] = []

    def acquire(self, request_count: int, token_count: int) -> float:
        self.acquired.append((request_count, token_count))
        return 0.0


def test_the_document_meter_books_each_batch_in_the_indexing_lane_before_calling():
    budget = RecordingBudget()
    embedder = FakeEmbedder()
    metered = MeteredDocumentEmbedder(embedder, budget)

    metered.embed_documents(["a", "b", "c"])

    assert budget.reservations == [(EmbeddingLane.INDEXING, 3)]
    assert embedder.calls == [["a", "b", "c"]]
    assert (metered.model_id, metered.dimensions, metered.task_type) == ("fake-model", 2, "TASK")
    assert metered.count_tokens("text") == 7


def test_a_refused_booking_never_reaches_gemini():
    embedder = FakeEmbedder()

    with pytest.raises(EmbeddingBudgetExhaustedError):
        MeteredDocumentEmbedder(embedder, RecordingBudget(refuse=True)).embed_documents(["a"])
    with pytest.raises(EmbeddingBudgetExhaustedError):
        MeteredQueryEmbedder(
            embedder, RecordingBudget(refuse=True), RecordingRateLimiter()
        ).embed_query("question")

    assert embedder.calls == []


def test_the_question_meter_books_one_and_paces_each_question():
    budget = RecordingBudget()
    rate_limiter = RecordingRateLimiter()

    MeteredQueryEmbedder(FakeEmbedder(), budget, rate_limiter).embed_query("x" * 30)

    assert budget.reservations == [(EmbeddingLane.QUESTIONS, 1)]
    assert rate_limiter.acquired == [(1, 10)]


class InMemoryQueryStore:
    def __init__(self) -> None:
        self.vectors: dict[str, list[float]] = {}

    def find_embedding(self, embedding_key: str) -> list[float] | None:
        return self.vectors.get(embedding_key)

    def save_embedding(self, embedding_key, embedder, vector) -> None:
        self.vectors[embedding_key] = vector


def test_a_cached_question_is_free():
    budget = RecordingBudget()
    metered = MeteredQueryEmbedder(FakeEmbedder(), budget, RecordingRateLimiter())
    caching_embedder = CachingQueryEmbedder(metered, InMemoryQueryStore())

    caching_embedder.embed_query("How is the cookie signed?")
    caching_embedder.embed_query("How is the cookie signed?")

    assert budget.reservations == [(EmbeddingLane.QUESTIONS, 1)]
    assert (caching_embedder.miss_count, caching_embedder.hit_count) == (1, 1)


class RefusingRateLimiter:
    def acquire(self, request_count: int, token_count: int) -> float:
        raise RateLimitWaitTooLongError(12.0, 5.0)


def test_a_question_the_per_minute_limit_refuses_books_no_seat():
    budget = RecordingBudget()
    embedder = FakeEmbedder()

    with pytest.raises(RateLimitWaitTooLongError):
        MeteredQueryEmbedder(embedder, budget, RefusingRateLimiter()).embed_query("question")

    assert budget.reservations == []
    assert embedder.calls == []
