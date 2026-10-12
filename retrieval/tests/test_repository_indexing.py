"""Tests for the shared indexing steps, on a small checkout written to a temporary folder, with a
fake clone, a fake embedder, and an in-memory store, so nothing reaches the network."""

from __future__ import annotations

import shutil
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from retrieval import repository_indexing
from retrieval.chunk_store import (
    CHUNKS_COLLECTION,
    CachedEmbedding,
    IndexedVersion,
    StoredChunk,
    repository_document,
)
from retrieval.config import INDEX_FORMAT_VERSION
from retrieval.fingerprint_store import StoredFingerprints
from retrieval.fingerprints import PathFingerprint, build_version_fingerprints, fingerprint_bytes
from retrieval.github_urls import RepositoryReference
from retrieval.other_languages import OtherLanguageCount
from retrieval.rate_limiter import RateLimiter
from retrieval.refresh_planning import FullIndexReason
from retrieval.repository_cloner import ClonedRepository, CloneMetadata, RepositoryNotPublicError
from retrieval.repository_indexing import (
    IndexingStage,
    NoPythonCodeError,
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
    """An in-memory chunk store whose contents outlive each run, like Atlas, so a second run can
    patch what the first stored. The fixture empties it before each test."""

    instances: list[RecordingChunkStore] = []
    chunks: dict[str, StoredChunk] = {}
    repositories: list[IndexedVersion] = []
    written_chunk_ids: list[str] = []
    calls_updates: list[dict[str, list[str]]] = []
    commit_updates: list[str] = []

    def __init__(self, database) -> None:
        self.database = database
        self.ensured_indexes = False
        RecordingChunkStore.instances.append(self)

    @classmethod
    def empty(cls) -> None:
        cls.instances = []
        cls.chunks = {}
        cls.repositories = []
        cls.written_chunk_ids = []
        cls.calls_updates = []
        cls.commit_updates = []

    def ensure_indexes(self) -> None:
        self.ensured_indexes = True

    def find_cached_embeddings(self, embedding_keys: list[str]) -> dict[str, CachedEmbedding]:
        wanted_keys = set(embedding_keys)
        return {
            stored.embedding_key: stored.embedding
            for stored in self.chunks.values()
            if stored.embedding_key in wanted_keys
        }

    def upsert_chunks(self, stored_chunks: list[StoredChunk]) -> None:
        for stored in stored_chunks:
            self.chunks[stored.chunk_id] = stored
            self.written_chunk_ids.append(stored.chunk_id)

    def find_chunk_calls(self, repository: str, version: str) -> dict[str, list[str]]:
        return {
            chunk_id: list(stored.chunk.calls)
            for chunk_id, stored in self.chunks.items()
            if (stored.metadata.repository, stored.metadata.version) == (repository, version)
        }

    def update_chunk_calls(self, calls_by_chunk_id: dict[str, list[str]]) -> None:
        self.calls_updates.append(dict(calls_by_chunk_id))
        for chunk_id, calls in calls_by_chunk_id.items():
            stored = self.chunks[chunk_id]
            self.chunks[chunk_id] = replace(stored, chunk=replace(stored.chunk, calls=tuple(calls)))

    def set_commit_id(self, repository: str, version: str, commit_id: str) -> None:
        self.commit_updates.append(commit_id)
        for chunk_id, stored in self.chunks.items():
            new_metadata = replace(stored.metadata, commit_id=commit_id)
            self.chunks[chunk_id] = replace(stored, metadata=new_metadata)

    def delete_chunks_except(self, repository: str, version: str, kept_ids: set[str]) -> int:
        stale_ids = [chunk_id for chunk_id in self.chunks if chunk_id not in kept_ids]
        for chunk_id in stale_ids:
            del self.chunks[chunk_id]
        return len(stale_ids)

    def upsert_repository(self, indexed_version: IndexedVersion) -> None:
        self.repositories.append(indexed_version)

    def find_repository_record(self, repository: str, version: str) -> dict | None:
        if not self.repositories:
            return None
        return repository_document(self.repositories[-1])

    def chunk_collection_statistics(self) -> dict[str, int]:
        return COLLECTION_STATISTICS


class RecordingFingerprintStore:
    saved: list[tuple] = []

    def __init__(self, database) -> None:
        self.database = database

    def save(self, metadata, fingerprints, format_version) -> None:
        RecordingFingerprintStore.saved.append((metadata, fingerprints, format_version))

    def find(self, repository: str, version: str) -> StoredFingerprints | None:
        if not RecordingFingerprintStore.saved:
            return None
        metadata, fingerprints, format_version = RecordingFingerprintStore.saved[-1]
        return StoredFingerprints(
            repository=metadata.repository,
            version=metadata.version,
            commit_id=metadata.commit_id,
            format_version=format_version,
            fingerprints=fingerprints,
        )


@pytest.fixture
def ensured_search_indexes(monkeypatch) -> list[tuple]:
    RecordingChunkStore.empty()
    RecordingFingerprintStore.saved = []
    monkeypatch.setattr(repository_indexing, "MongoChunkStore", RecordingChunkStore)
    monkeypatch.setattr(repository_indexing, "MongoFingerprintStore", RecordingFingerprintStore)
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


def _fake_clone(
    tmp_path: Path,
    clone_calls: list[tuple],
    files: dict[str, str] = SOURCE_FILES,
    metadata: CloneMetadata = METADATA,
):
    source_path = tmp_path / "source"
    if source_path.exists():
        shutil.rmtree(source_path)
    for relative_path, text in files.items():
        file_path = source_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text)

    def clone(reference, version):
        clone_calls.append((reference, version))
        return ClonedRepository(
            path=tmp_path, source_path=source_path, metadata=metadata, was_reused=False
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
        "check indexes, alongside chunking",
        "wait for index check",
        "compare with stored index",
        "embed and store",
        "store fingerprints",
    ]


def test_fingerprints_of_the_indexed_files_are_stored_after_the_version_is_recorded(
    tmp_path, ensured_search_indexes
):
    run = index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        FakeEmbedder(),
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, []),
    )

    expected_files = [
        PathFingerprint(path, fingerprint_bytes(text.encode("utf-8")))
        for path, text in sorted(SOURCE_FILES.items())
    ]
    assert run.fingerprints == build_version_fingerprints(expected_files)
    assert RecordingFingerprintStore.saved == [(METADATA, run.fingerprints, INDEX_FORMAT_VERSION)]
    assert len(RecordingChunkStore.instances[0].repositories) == 1


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


def test_the_index_check_runs_alongside_chunking(tmp_path, ensured_search_indexes, monkeypatch):
    """Chunking waits for a signal the index check sends; were the check to run after chunking,
    as it once did, chunking would never get the signal."""
    index_check_started = threading.Event()
    chunked_while_checking: list[bool] = []
    real_vector_check = repository_indexing.ensure_vector_index
    real_chunking = repository_indexing.chunk_source_files

    def signalling_vector_check(collection, dimensions):
        index_check_started.set()
        return real_vector_check(collection, dimensions)

    def waiting_chunking(files):
        chunked_while_checking.append(index_check_started.wait(timeout=5))
        return real_chunking(files)

    monkeypatch.setattr(repository_indexing, "ensure_vector_index", signalling_vector_check)
    monkeypatch.setattr(repository_indexing, "chunk_source_files", waiting_chunking)

    run = index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        FakeEmbedder(),
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, []),
    )

    assert chunked_while_checking == [True]
    assert run.vector_index_change == SearchIndexChange.UNCHANGED


def test_a_failed_index_check_stops_the_run_before_anything_is_stored(
    tmp_path, ensured_search_indexes, monkeypatch
):
    embedder = FakeEmbedder()

    def failing_keyword_check(collection):
        raise RuntimeError("Atlas could not list the search indexes")

    monkeypatch.setattr(repository_indexing, "ensure_keyword_index", failing_keyword_check)

    with pytest.raises(RuntimeError, match="could not list the search indexes"):
        index_repository_version(
            REFERENCE,
            "1.0.0",
            {CHUNKS_COLLECTION: "chunks collection"},
            embedder,
            RateLimiter(100, 30_000),
            clone=_fake_clone(tmp_path, []),
        )

    assert embedder.embedded_batches == []
    assert RecordingChunkStore.chunks == {}
    assert RecordingChunkStore.repositories == []
    assert RecordingFingerprintStore.saved == []


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
    assert RecordingFingerprintStore.saved == []


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
    assert RecordingChunkStore.chunks == {}
    assert RecordingChunkStore.repositories == []
    assert RecordingFingerprintStore.saved == []


def test_a_version_with_no_python_code_is_refused_before_any_embedding(
    tmp_path, ensured_search_indexes
):
    embedder = FakeEmbedder()
    files = {
        "src/index.js": "export const answer = 42;\n",
        "src/util.js": "export function noop() {}\n",
        "build.sh": "echo build\n",
        "README.md": "# Not Python\n",
    }

    with pytest.raises(NoPythonCodeError) as refusal:
        index_repository_version(
            REFERENCE,
            "1.0.0",
            {CHUNKS_COLLECTION: "chunks collection"},
            embedder,
            RateLimiter(100, 30_000),
            clone=_fake_clone(tmp_path, [], files),
        )

    assert str(refusal.value) == (
        "owner/project at 1.0.0 has no Python code to index "
        "(it has 2 JavaScript files and 1 Shell file); only Python is supported for now"
    )
    assert embedder.embedded_batches == []
    assert RecordingChunkStore.chunks == {}
    assert RecordingChunkStore.repositories == []
    assert RecordingFingerprintStore.saved == []


def test_files_in_other_languages_are_recorded_with_the_version(tmp_path, ensured_search_indexes):
    files = {**SOURCE_FILES, "pkg/_speedups.c": "int main(void) { return 0; }\n"}

    run = index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        FakeEmbedder(),
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, [], files),
    )

    expected = (OtherLanguageCount(language="C", files=1),)
    assert run.other_languages == expected
    assert RecordingChunkStore.instances[0].repositories[0].other_languages == expected


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


NEXT_COMMIT_METADATA = replace(METADATA, commit_id="1" * 40)

CALLING_FILES = {
    "pkg/app.py": "from pkg.helpers import helper\n\n\ndef run():\n    return helper()\n",
    "pkg/helpers.py": "def helper():\n    return 1\n",
}


def _index_files(tmp_path, files, metadata=METADATA, embedder=None):
    return index_repository_version(
        REFERENCE,
        "1.0.0",
        {CHUNKS_COLLECTION: "chunks collection"},
        embedder or FakeEmbedder(),
        RateLimiter(100, 30_000),
        clone=_fake_clone(tmp_path, [], files, metadata),
    )


def _start_second_run() -> None:
    RecordingChunkStore.written_chunk_ids.clear()
    RecordingChunkStore.calls_updates.clear()
    RecordingChunkStore.commit_updates.clear()


def test_a_version_indexed_for_the_first_time_is_indexed_in_full(tmp_path, ensured_search_indexes):
    run = _index_files(tmp_path, SOURCE_FILES)

    assert run.refresh.full_index_reason == FullIndexReason.NOT_INDEXED_BEFORE
    assert run.refresh.comparison is None
    assert run.report.written_chunk_count == 3
    assert run.report.kept_chunk_count == 0
    assert RecordingChunkStore.commit_updates == []


def test_indexing_unchanged_files_again_writes_nothing_and_embeds_nothing(
    tmp_path, ensured_search_indexes
):
    _index_files(tmp_path, SOURCE_FILES)
    _start_second_run()
    embedder = FakeEmbedder()

    run = _index_files(tmp_path, SOURCE_FILES, NEXT_COMMIT_METADATA, embedder)

    assert run.refresh.full_index_reason is None
    assert not run.refresh.comparison.has_changes
    assert run.report.written_chunk_count == 0
    assert run.report.kept_chunk_count == 3
    assert embedder.embedded_batches == []
    assert RecordingChunkStore.written_chunk_ids == []
    assert RecordingChunkStore.commit_updates == ["1" * 40]
    assert {stored.metadata.commit_id for stored in RecordingChunkStore.chunks.values()} == {
        "1" * 40
    }
    assert RecordingChunkStore.repositories[-1].metadata.commit_id == "1" * 40
    assert RecordingChunkStore.repositories[-1].chunk_count == 3


def test_only_the_chunks_of_a_changed_file_are_written_and_embedded(
    tmp_path, ensured_search_indexes
):
    _index_files(tmp_path, SOURCE_FILES)
    _start_second_run()
    changed_files = {**SOURCE_FILES, "tests/test_app.py": "def test_greet():\n    assert 1\n"}
    embedder = FakeEmbedder()

    run = _index_files(tmp_path, changed_files, NEXT_COMMIT_METADATA, embedder)

    assert run.refresh.comparison.changed == ("tests/test_app.py",)
    assert run.report.written_chunk_count == 1
    assert run.report.kept_chunk_count == 2
    assert [len(batch) for batch in embedder.embedded_batches] == [1]
    assert RecordingChunkStore.written_chunk_ids == [
        "owner/project@1.0.0:tests/test_app.py:1:function:1"
    ]


def test_chunks_of_a_removed_file_are_deleted(tmp_path, ensured_search_indexes):
    _index_files(tmp_path, SOURCE_FILES)
    _start_second_run()
    fewer_files = {"pkg/app.py": SOURCE_FILES["pkg/app.py"]}

    run = _index_files(tmp_path, fewer_files, NEXT_COMMIT_METADATA)

    assert run.refresh.comparison.removed == ("tests/test_app.py",)
    assert run.report.deleted_stale_chunk_count == 1
    assert sorted(
        stored.chunk.qualified_name for stored in RecordingChunkStore.chunks.values()
    ) == [
        "greet",
        "make_greeting",
    ]


def test_a_call_link_that_changed_in_an_unchanged_file_is_updated_without_rewriting_it(
    tmp_path, ensured_search_indexes
):
    _index_files(tmp_path, CALLING_FILES)
    _start_second_run()
    renamed_helper = {**CALLING_FILES, "pkg/helpers.py": "def helper_v2():\n    return 1\n"}

    run = _index_files(tmp_path, renamed_helper, NEXT_COMMIT_METADATA)

    run_chunk_id = "owner/project@1.0.0:pkg/app.py:4:function:1"
    assert run.refresh.comparison.changed == ("pkg/helpers.py",)
    assert RecordingChunkStore.calls_updates == [{run_chunk_id: []}]
    assert run.report.updated_calls_count == 1
    assert run_chunk_id not in RecordingChunkStore.written_chunk_ids
    assert RecordingChunkStore.chunks[run_chunk_id].chunk.calls == ()
    assert RecordingChunkStore.repositories[-1].call_graph_edge_count == 0


def test_a_chunk_missing_from_the_store_is_written_again_rather_than_trusted(
    tmp_path, ensured_search_indexes
):
    _index_files(tmp_path, SOURCE_FILES)
    del RecordingChunkStore.chunks["owner/project@1.0.0:pkg/app.py:1:function:1"]
    _start_second_run()

    run = _index_files(tmp_path, SOURCE_FILES, NEXT_COMMIT_METADATA)

    assert not run.refresh.comparison.has_changes
    assert RecordingChunkStore.written_chunk_ids == ["owner/project@1.0.0:pkg/app.py:1:function:1"]
    assert run.report.kept_chunk_count == 2


def test_fingerprints_from_another_commit_mean_a_full_index(tmp_path, ensured_search_indexes):
    _index_files(tmp_path, SOURCE_FILES)
    metadata, fingerprints, format_version = RecordingFingerprintStore.saved[-1]
    RecordingFingerprintStore.saved.append(
        (replace(metadata, commit_id="f" * 40), fingerprints, format_version)
    )
    _start_second_run()

    run = _index_files(tmp_path, SOURCE_FILES, NEXT_COMMIT_METADATA)

    assert run.refresh.full_index_reason == FullIndexReason.OTHER_COMMIT
    assert run.report.written_chunk_count == 3
    assert RecordingChunkStore.commit_updates == []
