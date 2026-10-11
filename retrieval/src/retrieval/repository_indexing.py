"""Index one repository version from start to finish: the steps shared by the index script and the
retrieval service.

The steps, in order, each timed:
1. Clone the version, or reuse a saved clone. The cloner refuses private repositories, ones
   without an allowed open-source license or a license file, and ones over the size limit.
2. Walk the checkout for Python files, scan each for secrets and redact them, and chunk it.
3. Resolve calls between source definitions and attach each chunk's callees.
4. Refuse a version with more than 3,000 chunks before spending any embedding quota: at most 800
   texts a day may be embedded for indexing, so 3,000 new chunks is about four days.
5. Make sure the chunk store's indexes and the Atlas vector and keyword search indexes exist.
6. Embed and store the chunks, reusing cached embeddings, then record the version.
7. Store the fingerprints of the indexed files (a hash tree; see `fingerprints.py`), so a later
   refresh can tell which files changed.

Optional hooks hear each stage as it starts and the embedding progress, so a background job can
keep its status current.

What each step found and what the run cost come back in one record, so the caller decides how
to show it. The database, embedder, and rate limiter are passed in, so the service can share
them across jobs and tests can pass stand-ins.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pymongo.database import Database

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.code_graph import RESOLVED_OUTCOMES, attach_calls, build_call_graph, edge_count_of
from retrieval.config import INDEX_FORMAT_VERSION, MAX_REPOSITORY_CHUNKS
from retrieval.embedders import DocumentEmbedder
from retrieval.fingerprint_store import MongoFingerprintStore
from retrieval.fingerprints import VersionFingerprints, build_version_fingerprints
from retrieval.github_urls import RepositoryReference
from retrieval.indexer import IndexingReport, ProgressHook, index_chunks
from retrieval.other_languages import OtherLanguageCount, describe_counts
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import ClonedRepository, CloneMetadata, clone_repository
from retrieval.repository_walker import chunk_source_files, find_python_files
from retrieval.search_indexes import SearchIndexChange, ensure_keyword_index, ensure_vector_index

MILLISECONDS_PER_SECOND = 1000


class IndexingStage(StrEnum):
    CLONING = "cloning"
    CHUNKING = "chunking"
    CALL_GRAPH = "call graph"
    EMBEDDING = "embedding"


StageHook = Callable[[IndexingStage], None]


class TooManyChunksError(ValueError):
    """Raised before any embedding when a version has more chunks than indexing allows."""


class NoPythonCodeError(ValueError):
    """Raised before any embedding when a version has no Python code to index."""


@dataclass(frozen=True)
class RepositoryIndexingRun:
    metadata: CloneMetadata
    indexed_file_count: int
    skipped_path_count: int
    secret_finding_count: int
    resolved_call_count: int
    call_site_count: int
    call_graph_edge_count: int
    vector_index_change: SearchIndexChange
    keyword_index_change: SearchIndexChange
    report: IndexingReport
    collection_statistics: dict[str, int]
    timings: dict[str, float]
    fingerprints: VersionFingerprints
    other_languages: tuple[OtherLanguageCount, ...] = ()


def index_repository_version(
    reference: RepositoryReference,
    version: str,
    database: Database,
    embedder: DocumentEmbedder,
    rate_limiter: RateLimiter,
    clone: Callable[[RepositoryReference, str], ClonedRepository] = clone_repository,
    max_chunk_count: int = MAX_REPOSITORY_CHUNKS,
    on_stage: StageHook | None = None,
    on_progress: ProgressHook | None = None,
) -> RepositoryIndexingRun:
    announce_stage = on_stage or _ignore_stage
    timings: dict[str, float] = {}
    announce_stage(IndexingStage.CLONING)
    stage_started = time.perf_counter()
    cloned = clone(reference, version)
    timings["clone or reuse"] = _milliseconds_since(stage_started)
    announce_stage(IndexingStage.CHUNKING)
    stage_started = time.perf_counter()
    walk_result = find_python_files(cloned.source_path)
    chunking = chunk_source_files(walk_result.files)
    timings["walk, scan, chunk"] = _milliseconds_since(stage_started)
    _require_python_code(len(chunking.chunks), walk_result.other_languages, cloned.metadata)
    _require_chunk_count_within(len(chunking.chunks), max_chunk_count, cloned.metadata)
    fingerprints = build_version_fingerprints(chunking.file_fingerprints)
    announce_stage(IndexingStage.CALL_GRAPH)
    stage_started = time.perf_counter()
    call_graph = build_call_graph(chunking.chunks, chunking.file_calls)
    chunks = attach_calls(chunking.chunks, call_graph)
    timings["call graph"] = _milliseconds_since(stage_started)
    announce_stage(IndexingStage.EMBEDDING)
    store = MongoChunkStore(database)
    store.ensure_indexes()
    chunks_collection = database[CHUNKS_COLLECTION]
    vector_index_change = ensure_vector_index(chunks_collection, embedder.dimensions)
    keyword_index_change = ensure_keyword_index(chunks_collection)
    stage_started = time.perf_counter()
    report = index_chunks(
        cloned.metadata,
        chunks,
        embedder,
        store,
        rate_limiter,
        on_progress=on_progress,
        other_languages=walk_result.other_languages,
    )
    timings["embed and store"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    MongoFingerprintStore(database).save(cloned.metadata, fingerprints, INDEX_FORMAT_VERSION)
    timings["store fingerprints"] = _milliseconds_since(stage_started)
    resolved_call_count = sum(
        1 for resolved in call_graph.resolved_calls if resolved.outcome in RESOLVED_OUTCOMES
    )
    return RepositoryIndexingRun(
        metadata=cloned.metadata,
        indexed_file_count=len(walk_result.files) - len(chunking.unscannable),
        skipped_path_count=len(walk_result.skipped) + len(chunking.unscannable),
        secret_finding_count=len(chunking.secret_findings),
        resolved_call_count=resolved_call_count,
        call_site_count=len(call_graph.resolved_calls),
        call_graph_edge_count=edge_count_of(chunks),
        vector_index_change=vector_index_change,
        keyword_index_change=keyword_index_change,
        report=report,
        collection_statistics=store.chunk_collection_statistics(),
        timings=timings,
        fingerprints=fingerprints,
        other_languages=walk_result.other_languages,
    )


def _require_python_code(
    chunk_count: int, other_languages: tuple[OtherLanguageCount, ...], metadata: CloneMetadata
) -> None:
    if chunk_count > 0:
        return
    found = f" (it has {describe_counts(other_languages)})" if other_languages else ""
    raise NoPythonCodeError(
        f"{metadata.repository} at {metadata.version} has no Python code to index{found}; "
        "only Python is supported for now"
    )


def _require_chunk_count_within(
    chunk_count: int, max_chunk_count: int, metadata: CloneMetadata
) -> None:
    if chunk_count > max_chunk_count:
        raise TooManyChunksError(
            f"{metadata.repository} at {metadata.version} has {chunk_count:,} chunks; "
            f"indexing allows at most {max_chunk_count:,}"
        )


def _ignore_stage(stage: IndexingStage) -> None:
    return None


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
