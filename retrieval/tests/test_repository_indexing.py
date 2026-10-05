"""Tests for the shared indexing steps, on a small checkout written to a temporary folder, with a
fake clone, a fake embedder, and an in-memory store, so nothing reaches the network."""

from __future__ import annotations

from pathlib import Path

import pytest

from retrieval import repository_indexing
from retrieval.chunk_store import CHUNKS_COLLECTION, CachedEmbedding, IndexedVersion, StoredChunk
from retrieval.github_urls import RepositoryReference
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import ClonedRepository, CloneMetadata, RepositoryNotPublicError
from retrieval.repository_indexing import (
    IndexingStage,
    TooManyChunksError,
    index_repository_version,
)
from retrieval.search_indexes import SearchIndexChange

REFERENCE = RepositoryReference(owner="owner", name="project")

METADATA = CloneMetadata(
    repository="owner/project",
    version="1.0.0",
    commit_id="0" * 40,
    license_spdx_id="MIT",
    license_name="MIT License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-10-05T00:00:00+00:00",
    license_path="LICENSE",
)

SOURCE_FILES = {
    "pkg/app.py": (
        'def make_greeting(name):\n    return "Hello, " + name\n\n\n'
        "def greet(name):\n    return make_greeting(name)\n"
    ),
    "tests/test_app.py": "def test_greet():\n    assert True\n",
}

COLLECTION_STATISTICS = {"count": 3, "size": 100, "storage_size": 200, "index_size": 50}


class FakeEmbedder:
    def __init__(self, dimensions: int = 3) -> None:
        self.model_id = "fake-model"
        self.dimensions = dimensions
        self.task_type = "RETRIEVAL_DOCUMENT"
        self.embedded_batches: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embedded_batches.append(list(texts))
        return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts]

    def count_tokens(self, text: str) -> int:
        return 0


class RecordingChunkStore:
    instances: list[RecordingChunkStore] = []

    def __init__(self, database) -> None:
        self.database = database
        self.chunks: dict[str, StoredChunk] = {}
        self.repositories: list[IndexedVersion] = []
        self.ensured_indexes = False
        RecordingChunkStore.instances.append(self)

    def ensure_indexes(self) -> None:
        self.ensured_indexes = True

    def find_cached_embeddings(self, embedding_keys: list[str]) -> dict[str, CachedEmbedding]:
        return {}

    def upsert_chunks(self, stored_chunks: list[StoredChunk]) -> None:
        for stored in stored_chunks:
            self.chunks[stored.chunk_id] = stored

    def delete_chunks_except(self, repository: str, version: str, kept_ids: set[str]) -> int:
        return 0

    def upsert_repository(self, indexed_version: IndexedVersion) -> None:
        self.repositories.append(indexed_version)

    def chunk_collection_statistics(self) -> dict[str, int]:
        return COLLECTION_STATISTICS


@pytest.fixture
def ensured_search_indexes(monkeypatch) -> list[tuple]:
    RecordingChunkStore.instances = []
    monkeypatch.setattr(repository_indexing, "MongoChunkStore", RecordingChunkStore)
    ensured: list[tuple] = []

    def ensure_vector_index(collection, dimensions):
        ensured.append(("vector", collection, dimensions))
        return SearchIndexChange.UNCHANGED

    def ensure_keyword_index(collection):
        ensured.append(("keyword", collection))
        return SearchIndexChange.CREATED

    monkeypatch.setattr(repository_indexing, "ensure_vector_index", ensure_vector_index)
    monkeypatch.setattr(repository_indexing, "ensure_keyword_index", ensure_keyword_index)
    return ensured


def _fake_clone(tmp_path: Path, clone_calls: list[tuple]):
    source_path = tmp_path / "source"
    for relative_path, text in SOURCE_FILES.items():
        file_path = source_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text)

    def clone(reference, version):
        clone_calls.append((reference, version))
        return ClonedRepository(
            path=tmp_path, source_path=source_path, metadata=METADATA, was_reused=False
        )

    return clone


def test_a_version_is_cloned_chunked_graphed_embedded_and_recorded(
    tmp_path, ensured_search_indexes
):
    clone_calls: list[tuple] = []
    embedder = FakeEmbedder()

    run = index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        embedder,
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, clone_calls),
    )

    store = RecordingChunkStore.instances[0]
    assert clone_calls == [(REFERENCE, "1.0.0")]
    assert run.metadata == METADATA
    assert run.indexed_file_count == 2
    assert run.skipped_path_count == 0
    assert run.secret_finding_count == 0
    assert run.resolved_call_count == 1
    assert run.call_site_count == 1
    assert run.call_graph_edge_count == 1
    assert run.report.chunk_count == 3
    assert run.report.embedded_input_count == 3
    assert run.report.cache_hit_count == 0
    assert sorted(stored.chunk.qualified_name for stored in store.chunks.values()) == [
        "greet",
        "make_greeting",
        "test_greet",
    ]
    assert store.repositories[0].call_graph_edge_count == 1
    assert run.vector_index_change == SearchIndexChange.UNCHANGED
    assert run.keyword_index_change == SearchIndexChange.CREATED
    assert run.collection_statistics == COLLECTION_STATISTICS
    assert list(run.timings) == [
        "clone or reuse",
        "walk, scan, chunk",
        "call graph",
        "embed and store",
    ]


def test_store_and_search_indexes_are_ensured_before_embedding(tmp_path, ensured_search_indexes):
    embedder = FakeEmbedder(dimensions=5)

    index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        embedder,
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, []),
    )

    assert RecordingChunkStore.instances[0].ensured_indexes is True
    assert ensured_search_indexes == [
        ("vector", "chunks collection", 5),
        ("keyword", "chunks collection"),
    ]


def test_a_refused_clone_stops_before_any_embedding(tmp_path, ensured_search_indexes):
    embedder = FakeEmbedder()

    def refusing_clone(reference, version):
        raise RepositoryNotPublicError("owner/project is private")

    with pytest.raises(RepositoryNotPublicError):
        index_repository_version(
            REFERENCE,
            "1.0.0",
            {CHUNKS_COLLECTION: "chunks collection"},
            embedder,
            RateLimiter(100, 30_000),
            clone=refusing_clone,
        )

    assert embedder.embedded_batches == []
    assert RecordingChunkStore.instances == []


def test_stages_are_announced_in_order_and_progress_is_reported(tmp_path, ensured_search_indexes):
    stages: list[IndexingStage] = []
    progress: list[tuple[int, int]] = []

    index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        FakeEmbedder(),
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, []),
        on_stage=stages.append,
        on_progress=lambda embedded, to_embed: progress.append((embedded, to_embed)),
    )

    assert stages == [
        IndexingStage.CLONING,
        IndexingStage.CHUNKING,
        IndexingStage.CALL_GRAPH,
        IndexingStage.EMBEDDING,
    ]
    assert progress == [(0, 3), (3, 3)]


def test_too_many_chunks_is_refused_before_any_embedding(tmp_path, ensured_search_indexes):
    embedder = FakeEmbedder()

    with pytest.raises(TooManyChunksError, match="owner/project at 1.0.0 has 3 chunks"):
        index_repository_version(
            REFERENCE,
            "1.0.0",
            {CHUNKS_COLLECTION: "chunks collection"},
            embedder,
            RateLimiter(100, 30_000),
            clone=_fake_clone(tmp_path, []),
            max_chunk_count=2,
        )

    assert embedder.embedded_batches == []
    assert RecordingChunkStore.instances == []


def test_exactly_the_chunk_limit_is_allowed(tmp_path, ensured_search_indexes):
    run = index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        FakeEmbedder(),
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, []),
        max_chunk_count=3,
    )

    assert run.report.chunk_count == 3
