"""Share Gemini's daily embedding quota between indexing and questions, in two lanes.

The free tier allows 1,000 embedded texts a day for the whole project, and indexing one repository
can need all of them. Indexing may therefore use at most 800 a day; questions may use anything
left of the 1,000, so they always have at least 200, and more on days with little indexing.

The tally lives in MongoDB, one document per quota day (which starts at midnight Pacific, when
Gemini's quota resets), so it survives restarts and counts every process that embeds: the
service, the scripts, and the evaluation. Integration tests are not counted; Gemini's own
daily-quota error stays the backstop.

Seats are booked before each call to Gemini, never after, in two steps: make sure today's
document exists, then one conditional update that adds the call's texts only if both limits still
hold. MongoDB applies a single update to a single document atomically, so two callers can never
both take the last seat. A call that fails, and each retry, still counts: over-counting stops
early, which is the safe direction. A call is booked whole or not at all; a refusal says how
many seats are left, so the indexer can send a smaller last batch that uses them instead of
leaving up to one batch of the lane unused at the end of a day.

The meters wrap the Gemini embedders, so callers see an ordinary embedder. A question's cached
vector never reaches its meter, so cache hits stay free. The question meter also paces questions
to their per-minute lane (20 of Gemini's 100 a minute); indexing gets its 80 through the rate
limiter the indexer already takes. Documents are deleted 30 days after they are created, keeping a
month of usage to measure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from google import genai
from pymongo import ASCENDING
from pymongo.database import Database

from retrieval.config import (
    EMBEDDING_USAGE_TTL_SECONDS,
    EMBEDDINGS_PER_DAY,
    INDEXING_EMBEDDING_TOKENS_PER_MINUTE,
    INDEXING_EMBEDDINGS_PER_DAY,
    INDEXING_EMBEDDINGS_PER_MINUTE,
    QUESTION_EMBEDDING_TOKENS_PER_MINUTE,
    QUESTION_EMBEDDINGS_PER_MINUTE,
)
from retrieval.embedders import (
    DocumentEmbedder,
    EmbeddingRequestError,
    GeminiDocumentEmbedder,
    GeminiQueryEmbedder,
    QueryEmbedder,
)
from retrieval.embedding_inputs import estimate_tokens
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.quota_clock import quota_day
from retrieval.rate_limiter import RateLimiter

EMBEDDING_USAGE_COLLECTION = "embedding_usage"

TOTAL_FIELD = "total"

CREATED_AT_FIELD = "created_at"


class EmbeddingLane(StrEnum):
    INDEXING = "indexing"
    QUESTIONS = "questions"


class EmbeddingBudgetExhaustedError(EmbeddingRequestError):
    """Raised before calling Gemini when a lane's daily budget has no room for the call.

    It counts as a used-up daily quota, so every caller already handles it: the indexer stops
    and keeps what it stored, and the service says to retry after midnight Pacific.
    `seats_left` is how many embeddings the lane could still take today when the call was refused.
    """

    def __init__(self, message: str, seats_left: int = 0) -> None:
        super().__init__(message, status_code=None, retry_after_seconds=None)
        self.seats_left = seats_left

    @property
    def is_daily_quota_exhausted(self) -> bool:
        return True


@dataclass(frozen=True)
class EmbeddingUsage:
    day: str
    indexing: int
    questions: int
    total: int


class EmbeddingBudget(Protocol):
    def reserve(self, lane: EmbeddingLane, count: int) -> None: ...


class MongoEmbeddingBudget:
    def __init__(
        self,
        database: Database,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        daily_limit: int = EMBEDDINGS_PER_DAY,
        indexing_daily_limit: int = INDEXING_EMBEDDINGS_PER_DAY,
    ) -> None:
        self._usage = database[EMBEDDING_USAGE_COLLECTION]
        self._clock = clock
        self._daily_limit = daily_limit
        self._indexing_daily_limit = indexing_daily_limit

    def ensure_indexes(self) -> None:
        self._usage.create_index(
            [(CREATED_AT_FIELD, ASCENDING)], expireAfterSeconds=EMBEDDING_USAGE_TTL_SECONDS
        )

    def reserve(self, lane: EmbeddingLane, count: int) -> None:
        """Book `count` embeddings in `lane` for today, or raise if either limit would be passed."""
        lane_limit = self._lane_limit(lane)
        if not 1 <= count <= lane_limit:
            raise ValueError(f"A call of {count} embeddings can never fit a limit of {lane_limit}")
        now = self._clock()
        day = quota_day(now)
        self._ensure_day(day, now)
        conditions = {"_id": day, TOTAL_FIELD: {"$lte": self._daily_limit - count}}
        if lane == EmbeddingLane.INDEXING:
            conditions[lane.value] = {"$lte": self._indexing_daily_limit - count}
        result = self._usage.update_one(
            conditions, {"$inc": {lane.value: count, TOTAL_FIELD: count}}
        )
        if result.matched_count == 0:
            raise EmbeddingBudgetExhaustedError(
                self._exhausted_message(lane, count), seats_left=self._seats_left(lane, day)
            )

    def usage_today(self) -> EmbeddingUsage:
        day = quota_day(self._clock())
        document = self._usage.find_one({"_id": day}) or {}
        return EmbeddingUsage(
            day=day,
            indexing=document.get(EmbeddingLane.INDEXING.value, 0),
            questions=document.get(EmbeddingLane.QUESTIONS.value, 0),
            total=document.get(TOTAL_FIELD, 0),
        )

    def _ensure_day(self, day: str, now: datetime) -> None:
        empty_day = {
            EmbeddingLane.INDEXING.value: 0,
            EmbeddingLane.QUESTIONS.value: 0,
            TOTAL_FIELD: 0,
            CREATED_AT_FIELD: now,
        }
        self._usage.update_one({"_id": day}, {"$setOnInsert": empty_day}, upsert=True)

    def _seats_left(self, lane: EmbeddingLane, day: str) -> int:
        document = self._usage.find_one({"_id": day}) or {}
        seats_left = self._daily_limit - document.get(TOTAL_FIELD, 0)
        if lane == EmbeddingLane.INDEXING:
            lane_seats_left = self._indexing_daily_limit - document.get(lane.value, 0)
            seats_left = min(seats_left, lane_seats_left)
        return max(seats_left, 0)

    def _lane_limit(self, lane: EmbeddingLane) -> int:
        if lane == EmbeddingLane.INDEXING:
            return min(self._indexing_daily_limit, self._daily_limit)
        return self._daily_limit

    def _exhausted_message(self, lane: EmbeddingLane, count: int) -> str:
        if lane == EmbeddingLane.INDEXING:
            return (
                f"No room for {count} more embeddings today: indexing may use at most "
                f"{self._indexing_daily_limit} of the {self._daily_limit} a day; "
                "the budget resets at midnight Pacific"
            )
        return (
            f"The daily embedding budget of {self._daily_limit} is used up; "
            "it resets at midnight Pacific"
        )


class MeteredDocumentEmbedder:
    """A document embedder that books every call in the indexing lane before making it."""

    def __init__(self, embedder: DocumentEmbedder, budget: EmbeddingBudget) -> None:
        self._embedder = embedder
        self._budget = budget
        self.model_id = embedder.model_id
        self.dimensions = embedder.dimensions
        self.task_type = embedder.task_type

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self._budget.reserve(EmbeddingLane.INDEXING, len(texts))
        return self._embedder.embed_documents(texts)

    def count_tokens(self, text: str) -> int:
        return self._embedder.count_tokens(text)

    def count_batch_tokens(self, texts: list[str]) -> int:
        return self._embedder.count_batch_tokens(texts)


class MeteredQueryEmbedder:
    """A query embedder that books each question in the questions lane and paces it per minute."""

    def __init__(
        self, embedder: QueryEmbedder, budget: EmbeddingBudget, rate_limiter: RateLimiter
    ) -> None:
        self._embedder = embedder
        self._budget = budget
        self._rate_limiter = rate_limiter
        self.model_id = embedder.model_id
        self.dimensions = embedder.dimensions
        self.task_type = embedder.task_type

    def embed_query(self, question: str) -> list[float]:
        """Take a per-minute slot first, then book the day's seat, so a question the per-minute
        limit refuses never uses a seat it did not spend."""
        self._rate_limiter.acquire(1, estimate_tokens(question))
        self._budget.reserve(EmbeddingLane.QUESTIONS, 1)
        return self._embedder.embed_query(question)


def build_query_embedder(
    database: Database,
    gemini_client: genai.Client,
    max_rate_limit_wait_seconds: float | None = None,
) -> CachingQueryEmbedder:
    """The question embedder every caller uses: the cache first, then the metered Gemini call.
    With `max_rate_limit_wait_seconds`, a question that would wait longer than that for a
    per-minute slot is refused at once instead."""
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    question_rate_limiter = RateLimiter(
        QUESTION_EMBEDDINGS_PER_MINUTE,
        QUESTION_EMBEDDING_TOKENS_PER_MINUTE,
        max_wait_seconds=max_rate_limit_wait_seconds,
    )
    metered_embedder = MeteredQueryEmbedder(
        GeminiQueryEmbedder(gemini_client), _budget_for(database), question_rate_limiter
    )
    return CachingQueryEmbedder(metered_embedder, query_embedding_store)


def build_document_embedder(
    database: Database, gemini_client: genai.Client
) -> MeteredDocumentEmbedder:
    return MeteredDocumentEmbedder(GeminiDocumentEmbedder(gemini_client), _budget_for(database))


def build_indexing_rate_limiter() -> RateLimiter:
    return RateLimiter(INDEXING_EMBEDDINGS_PER_MINUTE, INDEXING_EMBEDDING_TOKENS_PER_MINUTE)


def _budget_for(database: Database) -> MongoEmbeddingBudget:
    budget = MongoEmbeddingBudget(database)
    budget.ensure_indexes()
    return budget
