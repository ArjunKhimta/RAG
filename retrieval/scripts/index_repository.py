"""Clone, scan, chunk, embed, and store a GitHub repository version in MongoDB Atlas.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/index_repository.py https://github.com/pallets/flask --version 3.1.3

Embeddings are cached by content hash in the chunks collection, so running it again for the same
version makes no embedding requests. Each chunk is stored with the symbols it calls, from the
repository's call graph. Also creates the Atlas vector and keyword search indexes if
they are missing, or updates them if their definitions changed. Prints what the run cost: cache
hits, requests, retries, estimated tokens, the time for each stage, and the chunk collection's
size. Exits 0 on success and 1 on any refusal or failure. Every printed line passes through the
redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time

from pymongo.errors import PyMongoError

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.code_graph import RESOLVED_OUTCOMES, attach_calls, build_call_graph, edge_count_of
from retrieval.config import (
    EMBEDDING_REQUESTS_PER_MINUTE,
    EMBEDDING_TOKENS_PER_MINUTE,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError, GeminiDocumentEmbedder
from retrieval.github_urls import InvalidRepositoryUrlError, parse_github_url
from retrieval.indexer import IndexingReport, index_chunks
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact
from retrieval.repository_cloner import RepositoryError, clone_repository
from retrieval.repository_walker import chunk_source_files, find_python_files
from retrieval.search_indexes import ensure_keyword_index, ensure_vector_index

MILLISECONDS_PER_SECOND = 1000

BYTES_PER_MEGABYTE = 1024 * 1024

LABEL_WIDTH = 28


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    timings: dict[str, float] = {}
    try:
        reference = parse_github_url(arguments.url)
        stage_started = time.perf_counter()
        cloned = clone_repository(reference, arguments.version)
        timings["clone or reuse"] = _milliseconds_since(stage_started)
        stage_started = time.perf_counter()
        walk_result = find_python_files(cloned.source_path)
        chunking = chunk_source_files(walk_result.files)
        timings["walk, scan, chunk"] = _milliseconds_since(stage_started)
        stage_started = time.perf_counter()
        call_graph = build_call_graph(chunking.chunks, chunking.file_calls)
        chunks = attach_calls(chunking.chunks, call_graph)
        timings["call graph"] = _milliseconds_since(stage_started)
        mongo_client = build_mongo_client()
        database = mongo_client[MONGODB_DATABASE]
        store = MongoChunkStore(database)
        store.ensure_indexes()
        embedder = GeminiDocumentEmbedder(build_gemini_client())
        chunks_collection = database[CHUNKS_COLLECTION]
        vector_index_change = ensure_vector_index(chunks_collection, embedder.dimensions)
        keyword_index_change = ensure_keyword_index(chunks_collection)
        rate_limiter = RateLimiter(EMBEDDING_REQUESTS_PER_MINUTE, EMBEDDING_TOKENS_PER_MINUTE)
        stage_started = time.perf_counter()
        report = index_chunks(cloned.metadata, chunks, embedder, store, rate_limiter)
        timings["embed and store"] = _milliseconds_since(stage_started)
        collection_statistics = store.chunk_collection_statistics()
    except EmbeddingRequestError as error:
        if error.is_daily_quota_exhausted:
            _print(
                "Stopped: the daily embedding quota is used up. Everything embedded so far is "
                "stored; run this again after the daily reset to resume from the cache."
            )
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    except (
        InvalidRepositoryUrlError,
        RepositoryError,
        MissingConfigError,
        PyMongoError,
    ) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    skipped_count = len(walk_result.skipped) + len(chunking.unscannable)
    resolved_call_count = sum(
        1 for resolved in call_graph.resolved_calls if resolved.outcome in RESOLVED_OUTCOMES
    )
    report_lines = [
        f"Repository  {cloned.metadata.repository} at {cloned.metadata.version} "
        f"({cloned.metadata.commit_id[:12]}, {cloned.metadata.license_spdx_id})",
        f"Embedding   {embedder.model_id}, {embedder.dimensions} dimensions, {embedder.task_type}",
        f"Vector index   {VECTOR_INDEX_NAME}: {vector_index_change}",
        f"Keyword index  {KEYWORD_INDEX_NAME}: {keyword_index_change}",
        "",
        _row("Python files indexed", len(walk_result.files) - len(chunking.unscannable)),
        _row("Paths skipped", skipped_count),
        _row("Secret findings redacted", len(chunking.secret_findings)),
        _row("Call sites resolved", f"{resolved_call_count} of {len(call_graph.resolved_calls)}"),
        _row("Call graph edges", edge_count_of(chunks)),
        "",
        *_report_lines(report),
        "",
        "Chunk collection in Atlas (all indexed versions)",
        _row("Documents", collection_statistics["count"]),
        _row("Data size", _megabytes(collection_statistics["size"])),
        _row("Storage size", _megabytes(collection_statistics["storage_size"])),
        _row("Index size", _megabytes(collection_statistics["index_size"])),
        "",
        "Timing",
        *[_row(stage, f"{milliseconds:.0f} ms") for stage, milliseconds in timings.items()],
    ]
    for line in report_lines:
        _print(line)
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="https://github.com/<owner>/<repo>")
    parser.add_argument("--version", required=True, help="tag, branch, or commit ID to index")
    return parser.parse_args()


def _report_lines(report: IndexingReport) -> list[str]:
    return [
        _row("Chunks", report.chunk_count),
        _row("Unique embedding inputs", report.unique_input_count),
        _row("Cache hits", report.cache_hit_count),
        _row("Inputs embedded", report.embedded_input_count),
        _row("Embedding requests", report.embedding_request_count),
        _row("Retries", report.retry_count),
        _row("Exact token counts", report.token_count_request_count),
        _row("Estimated tokens sent", report.estimated_tokens_sent),
        _row("Truncated chunks", report.truncated_chunk_count),
        _row("Stale chunks deleted", report.deleted_stale_chunk_count),
        _row("Rate-limit waiting", f"{report.rate_limit_wait_seconds:.1f} s"),
        _row("Retry waiting", f"{report.retry_wait_seconds:.1f} s"),
    ]


def _row(label: str, value: object) -> str:
    return f"  {label.ljust(LABEL_WIDTH)}{value}"


def _megabytes(size_bytes: int) -> str:
    return f"{size_bytes / BYTES_PER_MEGABYTE:.2f} MB"


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
