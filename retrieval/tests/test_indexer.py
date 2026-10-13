"""Indexer tests with a fake embedder, an in-memory store, and a fake clock, so nothing waits or
reaches the network."""

from __future__ import annotations

from dataclasses import replace

import pytest

from retrieval.chunk_store import CachedEmbedding, IndexedVersion, StoredChunk, chunk_id_for
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.embedders import EmbeddingRequestError
from retrieval.embedding_budget import (
    EmbeddingBudgetExhaustedError,
    EmbeddingLane,
    MeteredDocumentEmbedder,
)
from retrieval.embedding_inputs import estimate_tokens
from retrieval.indexer import IndexingSettings, KeptChunks, index_chunks
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import CloneMetadata

METADATA = CloneMetadata(
    repository="owner/project",
    version="1.0.0",
    commit_id="0" * 40,
    license_spdx_id="MIT",
    license_name="MIT License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-09-29T00:00:00+00:00",
)


class FakeEmbedder:
    def __init__(self, dimensions: int = 3, failures: list[EmbeddingRequestError] | None = None):
        self.model_id = "fake-model"
        self.dimensions = dimensions
        self.task_type = "RETRIEVAL_DOCUMENT"
        self.failures = list(failures or [])
        self.embedded_batches: list[list[str]] = []
        self.counted_texts: list[str] = []
        self.token_count_result = 0
        self.counted_batches: list[list[str]] = []
        self.exact_tokens_per_text: int | None = None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.failures:
            raise self.failures.pop(0)
        self.embedded_batches.append(list(texts))
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts]

    def count_tokens(self, text: str) -> int:
        self.counted_texts.append(text)
        return self.token_count_result

    def count_batch_tokens(self, texts: list[str]) -> int:
        self.counted_batches.append(list(texts))
        if self.exact_tokens_per_text is None:
            return sum(estimate_tokens(text) for text in texts)
        return self.exact_tokens_per_text * len(texts)


class InMemoryChunkStore:
    def __init__(self) -> None:
        self.chunks: dict[str, StoredChunk] = {}
        self.repositories: list[IndexedVersion] = []
        self.upsert_batches: list[int] = []

    def find_cached_embeddings(self, embedding_keys: list[str]) -> dict[str, CachedEmbedding]:
        wanted_keys = set(embedding_keys)
        return {
            stored.embedding_key: stored.embedding
            for stored in self.chunks.values()
            if stored.embedding_key in wanted_keys
        }

    def upsert_chunks(self, stored_chunks: list[StoredChunk]) -> None:
        self.upsert_batches.append(len(stored_chunks))
        for stored in stored_chunks:
            self.chunks[stored.chunk_id] = stored

    def delete_chunks_except(self, repository: str, version: str, kept_ids: set[str]) -> int:
        stale_ids = [
            chunk_id
            for chunk_id, stored in self.chunks.items()
            if stored.metadata.repository == repository
            and stored.metadata.version == version
            and chunk_id not in kept_ids
        ]
        for chunk_id in stale_ids:
            del self.chunks[chunk_id]
        return len(stale_ids)

    def update_chunk_calls(self, calls_by_chunk_id: dict[str, list[str]]) -> None:
        for chunk_id, calls in calls_by_chunk_id.items():
            stored = self.chunks[chunk_id]
            self.chunks[chunk_id] = replace(stored, chunk=replace(stored.chunk, calls=tuple(calls)))

    def set_commit_id(self, repository: str, version: str, commit_id: str) -> None:
        for chunk_id, stored in self.chunks.items():
            if (stored.metadata.repository, stored.metadata.version) == (repository, version):
                new_metadata = replace(stored.metadata, commit_id=commit_id)
                self.chunks[chunk_id] = replace(stored, metadata=new_metadata)

    def upsert_repository(self, indexed_version: IndexedVersion) -> None:
        self.repositories.append(indexed_version)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _function_chunk(index: int, text: str | None = None) -> CodeChunk:
    return CodeChunk(
        file_path="pkg/module.py",
        start_line=index * 10 + 1,
        end_line=index * 10 + 2,
        kind=ChunkKind.FUNCTION,
        name=f"function_{index}",
        qualified_name=f"function_{index}",
        parent_class=None,
        text=text or f"def function_{index}():\n    return {index}",
    )


def _index(chunks, embedder, store, clock=None, settings=None, jitter=lambda: 1.0):
    active_clock = clock or FakeClock()
    rate_limiter = RateLimiter(1000, 1_000_000, clock=active_clock.time, sleep=active_clock.sleep)
    return index_chunks(
        METADATA,
        chunks,
        embedder,
        store,
        rate_limiter,
        settings=settings or IndexingSettings(batch_size=100, batch_token_budget=1_000_000),
        sleep=active_clock.sleep,
        jitter_fraction=jitter,
    )


def test_every_chunk_is_embedded_and_stored_on_the_first_run():
    chunks = [_function_chunk(index) for index in range(5)]
    embedder = FakeEmbedder()
    store = InMemoryChunkStore()

    report = _index(chunks, embedder, store)

    assert len(store.chunks) == 5
    assert report.embedded_input_count == 5
    assert report.cache_hit_count == 0
    assert report.embedding_request_count == 1
    assert store.repositories[0].chunk_count == 5


def test_the_version_record_counts_the_call_graph_edges_of_its_chunks():
    chunks = [
        replace(_function_chunk(0), calls=("pkg/module.py::function_1",)),
        replace(
            _function_chunk(1),
            calls=("pkg/module.py::function_0", "pkg/module.py::function_2"),
        ),
        _function_chunk(2),
    ]

    store = InMemoryChunkStore()
    _index(chunks, FakeEmbedder(), store)

    assert store.repositories[0].call_graph_edge_count == 3


def test_calls_do_not_change_the_embedding_cache_key():
    store = InMemoryChunkStore()
    _index([_function_chunk(0)], FakeEmbedder(), store)
    embedder = FakeEmbedder()

    report = _index(
        [replace(_function_chunk(0), calls=("pkg/module.py::function_1",))], embedder, store
    )

    assert report.cache_hit_count == 1
    assert embedder.embedded_batches == []


def test_a_second_run_reuses_every_embedding_and_makes_no_requests():
    chunks = [_function_chunk(index) for index in range(5)]
    store = InMemoryChunkStore()
    _index(chunks, FakeEmbedder(), store)
    second_embedder = FakeEmbedder()

    report = _index(chunks, second_embedder, store)

    assert second_embedder.embedded_batches == []
    assert report.cache_hit_count == 5
    assert report.embedding_request_count == 0
    assert len(store.chunks) == 5


def test_identical_inputs_are_embedded_once_but_stored_for_every_chunk():
    shared_text = "def helper():\n    return 1"
    chunks = [
        replace(_function_chunk(0, shared_text), name="helper", qualified_name="helper"),
        replace(_function_chunk(1, shared_text), name="helper", qualified_name="helper"),
    ]
    embedder = FakeEmbedder()
    store = InMemoryChunkStore()

    report = _index(chunks, embedder, store)

    assert report.unique_input_count == 1
    assert sum(len(batch) for batch in embedder.embedded_batches) == 1
    assert len(store.chunks) == 2


def test_changing_the_dimensions_misses_the_cache():
    chunks = [_function_chunk(index) for index in range(3)]
    store = InMemoryChunkStore()
    _index(chunks, FakeEmbedder(dimensions=3), store)

    report = _index(chunks, FakeEmbedder(dimensions=4), store)

    assert report.cache_hit_count == 0
    assert report.embedded_input_count == 3


def test_batches_respect_both_the_size_and_the_token_budget():
    chunks = [_function_chunk(index) for index in range(7)]
    embedder = FakeEmbedder()

    _index(
        chunks,
        embedder,
        InMemoryChunkStore(),
        settings=IndexingSettings(batch_size=3, batch_token_budget=1_000_000),
    )
    size_limited_batches = [len(batch) for batch in embedder.embedded_batches]
    token_embedder = FakeEmbedder()
    _index(
        chunks,
        token_embedder,
        InMemoryChunkStore(),
        settings=IndexingSettings(batch_size=100, batch_token_budget=40),
    )

    assert size_limited_batches == [3, 3, 1]
    assert all(len(batch) < 7 for batch in token_embedder.embedded_batches)
    assert sum(len(batch) for batch in token_embedder.embedded_batches) == 7


def test_a_batch_whose_exact_count_is_over_the_budget_is_halved_until_each_part_fits():
    chunks = [_function_chunk(index) for index in range(8)]
    embedder = FakeEmbedder()
    embedder.exact_tokens_per_text = 200

    report = _index(
        chunks,
        embedder,
        InMemoryChunkStore(),
        settings=IndexingSettings(batch_size=100, batch_token_budget=1000),
    )

    assert [len(batch) for batch in embedder.counted_batches] == [8, 4, 4]
    assert [len(batch) for batch in embedder.embedded_batches] == [4, 4]
    assert report.batch_token_count_request_count == 3
    assert report.counted_tokens_sent == 1600
    assert report.embedded_input_count == 8


def test_the_rate_limiter_paces_batches_by_their_exact_token_count():
    chunks = [_function_chunk(index) for index in range(8)]
    embedder = FakeEmbedder()
    embedder.exact_tokens_per_text = 200
    clock = FakeClock()
    rate_limiter = RateLimiter(1000, 1000, clock=clock.time, sleep=clock.sleep)

    report = index_chunks(
        METADATA,
        chunks,
        embedder,
        InMemoryChunkStore(),
        rate_limiter,
        settings=IndexingSettings(batch_size=100, batch_token_budget=1000),
        sleep=clock.sleep,
    )

    assert report.embedding_request_count == 2
    assert report.rate_limit_wait_seconds == pytest.approx(60.0)


def test_a_single_input_over_the_budget_is_counted_once_and_not_split_further():
    embedder = FakeEmbedder()
    embedder.exact_tokens_per_text = 5000

    report = _index(
        [_function_chunk(0)],
        embedder,
        InMemoryChunkStore(),
        settings=IndexingSettings(batch_size=100, batch_token_budget=1000),
    )

    assert len(embedder.counted_batches) == 1
    assert [len(batch) for batch in embedder.embedded_batches] == [1]
    assert report.counted_tokens_sent == 5000


def test_each_batch_is_stored_as_soon_as_it_is_embedded():
    chunks = [_function_chunk(index) for index in range(5)]
    store = InMemoryChunkStore()

    _index(chunks, FakeEmbedder(), store, settings=IndexingSettings(batch_size=2))

    assert store.upsert_batches == [0, 2, 2, 1]


def test_an_interrupted_run_resumes_from_the_stored_batches():
    chunks = [_function_chunk(index) for index in range(4)]
    store = InMemoryChunkStore()
    failing_embedder = FakeEmbedder()
    permanent_error = EmbeddingRequestError(
        "bad request", status_code=400, retry_after_seconds=None
    )
    original_embed = failing_embedder.embed_documents

    def fail_on_second_batch(texts):
        if failing_embedder.embedded_batches:
            raise permanent_error
        return original_embed(texts)

    failing_embedder.embed_documents = fail_on_second_batch
    with pytest.raises(EmbeddingRequestError):
        _index(chunks, failing_embedder, store, settings=IndexingSettings(batch_size=2))
    resumed_embedder = FakeEmbedder()

    report = _index(chunks, resumed_embedder, store, settings=IndexingSettings(batch_size=2))

    assert report.cache_hit_count == 2
    assert sum(len(batch) for batch in resumed_embedder.embedded_batches) == 2
    assert len(store.chunks) == 4


def test_a_rate_limited_request_is_retried_after_the_suggested_delay():
    clock = FakeClock()
    rate_limited = EmbeddingRequestError("quota", status_code=429, retry_after_seconds=17.0)
    embedder = FakeEmbedder(failures=[rate_limited])

    report = _index([_function_chunk(0)], embedder, InMemoryChunkStore(), clock=clock)

    assert clock.sleeps == [17.0]
    assert report.retry_count == 1
    assert report.embedding_request_count == 2
    assert report.embedded_input_count == 1


def test_without_a_suggested_delay_retries_back_off_exponentially_with_jitter():
    clock = FakeClock()
    unavailable = EmbeddingRequestError("busy", status_code=503, retry_after_seconds=None)
    embedder = FakeEmbedder(failures=[unavailable, unavailable, unavailable])

    _index([_function_chunk(0)], embedder, InMemoryChunkStore(), clock=clock, jitter=lambda: 0.0)

    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_a_request_that_keeps_failing_gives_up_after_the_last_attempt():
    unavailable = EmbeddingRequestError("busy", status_code=503, retry_after_seconds=None)
    embedder = FakeEmbedder(failures=[unavailable] * 3)

    with pytest.raises(EmbeddingRequestError):
        _index(
            [_function_chunk(0)],
            embedder,
            InMemoryChunkStore(),
            settings=IndexingSettings(max_attempts=3),
        )


def test_an_exhausted_daily_quota_stops_at_once_and_keeps_earlier_batches():
    chunks = [_function_chunk(index) for index in range(4)]
    store = InMemoryChunkStore()
    clock = FakeClock()
    daily_quota = EmbeddingRequestError(
        "daily quota",
        status_code=429,
        retry_after_seconds=24.0,
        exceeded_quota_ids=("EmbedContentRequestsPerDayPerProjectPerModel-FreeTier",),
    )
    embedder = FakeEmbedder()
    original_embed = embedder.embed_documents

    def fail_on_second_batch(texts):
        if embedder.embedded_batches:
            raise daily_quota
        return original_embed(texts)

    embedder.embed_documents = fail_on_second_batch

    with pytest.raises(EmbeddingRequestError) as raised:
        _index(chunks, embedder, store, clock=clock, settings=IndexingSettings(batch_size=2))

    assert raised.value.is_daily_quota_exhausted
    assert clock.sleeps == []
    assert len(store.chunks) == 2


def test_each_text_in_a_batch_counts_against_the_requests_per_minute():
    chunks = [_function_chunk(index) for index in range(4)]
    clock = FakeClock()
    rate_limiter = RateLimiter(2, 1_000_000, clock=clock.time, sleep=clock.sleep)

    report = index_chunks(
        METADATA,
        chunks,
        FakeEmbedder(),
        InMemoryChunkStore(),
        rate_limiter,
        settings=IndexingSettings(batch_size=2, batch_token_budget=1_000_000),
        sleep=clock.sleep,
    )

    assert report.embedding_request_count == 2
    assert report.rate_limit_wait_seconds == pytest.approx(60.0)


def test_a_non_retryable_error_is_raised_immediately():
    bad_request = EmbeddingRequestError("bad", status_code=400, retry_after_seconds=None)
    clock = FakeClock()
    embedder = FakeEmbedder(failures=[bad_request])

    with pytest.raises(EmbeddingRequestError):
        _index([_function_chunk(0)], embedder, InMemoryChunkStore(), clock=clock)

    assert clock.sleeps == []


def test_tokens_are_counted_only_when_the_estimate_is_over_the_limit():
    short_chunk = _function_chunk(0)
    long_chunk = _function_chunk(1, text="x = 1\n" * 50)
    embedder = FakeEmbedder()
    embedder.token_count_result = 50

    report = _index(
        [short_chunk, long_chunk],
        embedder,
        InMemoryChunkStore(),
        settings=IndexingSettings(input_token_limit=60),
    )

    assert len(embedder.counted_texts) == 1
    assert "x = 1" in embedder.counted_texts[0]
    assert report.token_count_request_count == 1
    assert report.truncated_chunk_count == 0


def test_an_input_really_over_the_limit_is_embedded_and_marked_truncated():
    long_chunk = _function_chunk(0, text="x = 1\n" * 50)
    embedder = FakeEmbedder()
    embedder.token_count_result = 500
    store = InMemoryChunkStore()

    report = _index([long_chunk], embedder, store, settings=IndexingSettings(input_token_limit=60))

    stored = next(iter(store.chunks.values()))
    assert stored.embedding.is_truncated
    assert report.truncated_chunk_count == 1


def test_chunks_that_no_longer_exist_are_deleted_for_this_version_only():
    store = InMemoryChunkStore()
    _index([_function_chunk(index) for index in range(3)], FakeEmbedder(), store)
    other_version = replace(METADATA, version="2.0.0")
    clock = FakeClock()
    rate_limiter = RateLimiter(1000, 1_000_000, clock=clock.time, sleep=clock.sleep)
    index_chunks(other_version, [_function_chunk(9)], FakeEmbedder(), store, rate_limiter)

    report = _index([_function_chunk(0)], FakeEmbedder(), store)

    assert report.deleted_stale_chunk_count == 2
    remaining_versions = sorted(stored.metadata.version for stored in store.chunks.values())
    assert remaining_versions == ["1.0.0", "2.0.0"]


def test_split_parts_are_embedded_with_the_signature_they_lack():
    later_part = replace(
        _function_chunk(0, text="    return 1"),
        signature="def function_0():",
        part_number=2,
        part_count=2,
    )
    embedder = FakeEmbedder()

    _index([later_part], embedder, InMemoryChunkStore())

    assert "def function_0():\n    return 1" in embedder.embedded_batches[0][0]


def test_duplicate_chunk_ids_are_refused():
    chunk = _function_chunk(0)

    with pytest.raises(ValueError):
        _index([chunk, chunk], FakeEmbedder(), InMemoryChunkStore())


class SeatLimitedBudget:
    def __init__(self, seats: int) -> None:
        self.seats = seats
        self.refusals = 0

    def reserve(self, lane: EmbeddingLane, count: int) -> None:
        if count > self.seats:
            self.refusals += 1
            raise EmbeddingBudgetExhaustedError("No room today")
        self.seats -= count


def test_a_used_up_budget_stops_at_once_keeps_what_is_stored_and_resumes_next_day():
    chunks = [_function_chunk(index) for index in range(6)]
    store = InMemoryChunkStore()
    budget = SeatLimitedBudget(seats=4)
    embedder = FakeEmbedder()

    with pytest.raises(EmbeddingBudgetExhaustedError):
        _index(
            chunks,
            MeteredDocumentEmbedder(embedder, budget),
            store,
            settings=IndexingSettings(batch_size=2),
        )

    assert budget.refusals == 1
    assert len(store.chunks) == 4
    assert store.repositories == []
    next_day_embedder = FakeEmbedder()
    report = _index(
        chunks,
        MeteredDocumentEmbedder(next_day_embedder, SeatLimitedBudget(seats=800)),
        store,
        settings=IndexingSettings(batch_size=2),
    )
    assert report.cache_hit_count == 4
    assert sum(len(batch) for batch in next_day_embedder.embedded_batches) == 2
    assert len(store.repositories) == 1


def test_default_batches_fit_the_indexing_lane_per_minute():
    settings = IndexingSettings()

    assert settings.batch_size == 80
    assert settings.batch_token_budget == 24_000


def test_progress_counts_from_zero_after_the_cache_and_after_each_batch():
    chunks = [_function_chunk(index) for index in range(5)]
    store = InMemoryChunkStore()
    _index(chunks[:1], FakeEmbedder(), store)
    progress: list[tuple[int, int]] = []
    active_clock = FakeClock()

    index_chunks(
        METADATA,
        chunks,
        FakeEmbedder(),
        store,
        RateLimiter(1000, 1_000_000, clock=active_clock.time, sleep=active_clock.sleep),
        settings=IndexingSettings(batch_size=2, batch_token_budget=1_000_000),
        sleep=active_clock.sleep,
        on_progress=lambda embedded, to_embed: progress.append((embedded, to_embed)),
    )

    assert progress == [(0, 4), (2, 4), (4, 4)]


def test_each_embedding_retry_and_the_give_up_are_logged_with_their_reason(caplog):
    timed_out = EmbeddingRequestError(
        "Gemini request timed out after 30 s",
        status_code=None,
        retry_after_seconds=None,
        reason="timed out after 30 s",
        is_transport_failure=True,
    )
    embedder = FakeEmbedder(failures=[timed_out] * 2)

    with pytest.raises(EmbeddingRequestError):
        _index(
            [_function_chunk(0)],
            embedder,
            InMemoryChunkStore(),
            clock=FakeClock(),
            jitter=lambda: 0.0,
            settings=IndexingSettings(max_attempts=2),
        )

    assert caplog.messages == [
        "Gemini embedding attempt 1 of 2 for 1 texts failed (timed out after 30 s); "
        "retrying in 1.0 s",
        "Gemini embedding of 1 texts gave up after 2 attempts (timed out after 30 s)",
    ]


def test_a_retry_log_names_the_quotas_gemini_says_were_exceeded(caplog):
    over_quota = EmbeddingRequestError(
        "Gemini request failed",
        status_code=429,
        retry_after_seconds=None,
        reason="HTTP 429 RESOURCE_EXHAUSTED",
        exceeded_quota_ids=("ExampleTokensPerMinute",),
    )
    embedder = FakeEmbedder(failures=[over_quota])

    _index([_function_chunk(0)], embedder, InMemoryChunkStore(), jitter=lambda: 0.0)

    assert caplog.messages == [
        "Gemini embedding attempt 1 of 6 for 1 texts failed "
        "(HTTP 429 RESOURCE_EXHAUSTED; quota ExampleTokensPerMinute); retrying in 1.0 s",
    ]


def test_kept_chunks_are_neither_embedded_nor_rewritten_but_are_counted_and_recorded():
    store = InMemoryChunkStore()
    chunks = [_function_chunk(index) for index in range(3)]
    _index(chunks, FakeEmbedder(), store)
    next_commit = replace(METADATA, commit_id="1" * 40)
    changed_chunks = [*chunks[:2], _function_chunk(2, text="def function_2():\n    return 22")]
    kept_ids = frozenset(chunk_id_for(next_commit, chunk) for chunk in chunks[:2])
    embedder = FakeEmbedder()
    store.upsert_batches.clear()
    clock = FakeClock()

    report = index_chunks(
        next_commit,
        changed_chunks,
        embedder,
        store,
        RateLimiter(1000, 1_000_000, clock=clock.time, sleep=clock.sleep),
        kept=KeptChunks(chunk_ids=kept_ids, calls_to_update={}),
    )

    assert report.written_chunk_count == 1
    assert report.kept_chunk_count == 2
    assert report.unique_input_count == 1
    assert [len(batch) for batch in embedder.embedded_batches] == [1]
    assert sum(store.upsert_batches) == 1
    assert store.repositories[-1].chunk_count == 3
    assert store.repositories[-1].metadata.commit_id == "1" * 40
    assert {stored.metadata.commit_id for stored in store.chunks.values()} == {"1" * 40}


def test_kept_chunks_get_their_new_calls_and_the_record_counts_every_edge():
    store = InMemoryChunkStore()
    chunks = [_function_chunk(0), _function_chunk(1)]
    _index(chunks, FakeEmbedder(), store)
    caller_id = chunk_id_for(METADATA, chunks[0])
    linked_chunks = [replace(chunks[0], calls=("pkg/module.py::function_1",)), chunks[1]]
    clock = FakeClock()

    report = index_chunks(
        METADATA,
        linked_chunks,
        FakeEmbedder(),
        store,
        RateLimiter(1000, 1_000_000, clock=clock.time, sleep=clock.sleep),
        kept=KeptChunks(
            chunk_ids=frozenset(chunk_id_for(METADATA, chunk) for chunk in chunks),
            calls_to_update={caller_id: ["pkg/module.py::function_1"]},
        ),
    )

    assert report.updated_calls_count == 1
    assert report.written_chunk_count == 0
    assert store.chunks[caller_id].chunk.calls == ("pkg/module.py::function_1",)
    assert store.repositories[-1].call_graph_edge_count == 1
