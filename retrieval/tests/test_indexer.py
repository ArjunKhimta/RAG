"""Indexer tests with a fake embedder, an in-memory store, and a fake clock, so nothing waits or
reaches the network."""

from __future__ import annotations

from dataclasses import replace

import pytest

from retrieval.chunk_store import CachedEmbedding, IndexedVersion, StoredChunk
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.embedders import EmbeddingRequestError
from retrieval.indexer import IndexingSettings, index_chunks
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

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.failures:
            raise self.failures.pop(0)
        self.embedded_batches.append(list(texts))
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts]

    def count_tokens(self, text: str) -> int:
        self.counted_texts.append(text)
        return self.token_count_result


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

    report = _index(
        [long_chunk], embedder, store, settings=IndexingSettings(input_token_limit=60)
    )

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
