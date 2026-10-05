"""Index one repository version from start to finish: the steps shared by the index script and the
retrieval service.

The steps, in order, each timed:
1. Clone the version, or reuse a saved clone. The cloner refuses private repositories, ones
   without an allowed open-source license or a license file, and ones over the size limit.
2. Walk the checkout for Python files, scan each for secrets and redact them, and chunk it.
3. Resolve calls between source definitions and attach each chunk's callees.
4. Make sure the chunk store's indexes and the Atlas vector and keyword search indexes exist.
5. Embed and store the chunks, reusing cached embeddings, then record the version.

What each step found and what the run cost come back in one record, so the caller decides how
to show it. The database, embedder, and rate limiter are passed in, so the service can share
them across jobs and tests can pass stand-ins.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from pymongo.database import Database

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.code_graph import RESOLVED_OUTCOMES, attach_calls, build_call_graph, edge_count_of
from retrieval.embedders import DocumentEmbedder
from retrieval.github_urls import RepositoryReference
from retrieval.indexer import IndexingReport, index_chunks
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import ClonedRepository, CloneMetadata, clone_repository
from retrieval.repository_walker import chunk_source_files, find_python_files
from retrieval.search_indexes import SearchIndexChange, ensure_keyword_index, ensure_vector_index

MILLISECONDS_PER_SECOND = 1000


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


def index_repository_version(
    reference: RepositoryReference,
    version: str,
    database: Database,
    embedder: DocumentEmbedder,
    rate_limiter: RateLimiter,
    clone: Callable[[RepositoryReference, str], ClonedRepository] = clone_repository,
) -> RepositoryIndexingRun:
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    cloned = clone(reference, version)
    timings["clone or reuse"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    walk_result = find_python_files(cloned.source_path)
    chunking = chunk_source_files(walk_result.files)
    timings["walk, scan, chunk"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    call_graph = build_call_graph(chunking.chunks, chunking.file_calls)
    chunks = attach_calls(chunking.chunks, call_graph)
    timings["call graph"] = _milliseconds_since(stage_started)
    store = MongoChunkStore(database)
    store.ensure_indexes()
    chunks_collection = database[CHUNKS_COLLECTION]
    vector_index_change = ensure_vector_index(chunks_collection, embedder.dimensions)
    keyword_index_change = ensure_keyword_index(chunks_collection)
    stage_started = time.perf_counter()
    report = index_chunks(cloned.metadata, chunks, embedder, store, rate_limiter)
    timings["embed and store"] = _milliseconds_since(stage_started)
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
    )


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
