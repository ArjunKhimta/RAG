"""Book embedding seats in real MongoDB Atlas from many threads at once. Excluded from the default
run; makes no Gemini requests.

    pytest retrieval/tests -m integration

Writes only to the `code_search_test` database, which is dropped afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from retrieval.clients import build_mongo_client
from retrieval.config import MONGODB_URI_VARIABLE, load_environment
from retrieval.embedding_budget import (
    EmbeddingBudgetExhaustedError,
    EmbeddingLane,
    MongoEmbeddingBudget,
)

pytestmark = pytest.mark.integration

TEST_DATABASE = "code_search_test"

NOON_PACIFIC = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

THREAD_COUNT = 16


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    if not os.environ.get(MONGODB_URI_VARIABLE):
        pytest.skip(f"{MONGODB_URI_VARIABLE} is not configured")


@pytest.fixture
def budget() -> Iterator[MongoEmbeddingBudget]:
    mongo_client = build_mongo_client()
    mongo_client.drop_database(TEST_DATABASE)
    embedding_budget = MongoEmbeddingBudget(mongo_client[TEST_DATABASE], clock=lambda: NOON_PACIFIC)
    embedding_budget.ensure_indexes()
    yield embedding_budget
    mongo_client.drop_database(TEST_DATABASE)
    mongo_client.close()


def _book_concurrently(
    budget: MongoEmbeddingBudget, lane: EmbeddingLane, count: int, attempts: int
):
    def book(_attempt: int) -> bool:
        try:
            budget.reserve(lane, count)
        except EmbeddingBudgetExhaustedError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=THREAD_COUNT) as executor:
        return sum(executor.map(book, range(attempts)))


def test_concurrent_indexing_batches_never_pass_eight_hundred(budget):
    booked_batches = _book_concurrently(budget, EmbeddingLane.INDEXING, 80, attempts=16)

    assert booked_batches == 10
    usage = budget.usage_today()
    assert (usage.indexing, usage.questions, usage.total) == (800, 0, 800)


def test_concurrent_questions_take_exactly_the_seats_left(budget):
    budget.reserve(EmbeddingLane.INDEXING, 800)

    booked_questions = _book_concurrently(budget, EmbeddingLane.QUESTIONS, 1, attempts=240)

    assert booked_questions == 200
    usage = budget.usage_today()
    assert (usage.indexing, usage.questions, usage.total) == (800, 200, 1000)
