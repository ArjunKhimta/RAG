"""Clone, scan, chunk, embed, and store a GitHub repository version in MongoDB Atlas.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/index_repository.py https://github.com/pallets/flask --version 3.1.3

A saved clone of the version is reused. With `--refresh`, a tag or branch is first looked up on
GitHub (`git ls-remote`), and a saved clone at another commit is replaced, so a branch that has
moved is indexed as it is now; a commit ID never moves and is reused as it is.

Embeddings are cached by content hash in the chunks collection, so running it again for the same
version makes no embedding requests. Indexing a version again patches what is stored when its
fingerprints allow: chunks of unchanged files are kept, not rewritten. Each chunk is stored with
the symbols it calls, from the repository's call graph. Also creates the Atlas vector and keyword
search indexes if they are missing, or updates them if their definitions changed. Prints what the
run cost: cache hits, requests, retries, estimated tokens, the time for each stage, and the chunk
collection's size. Exits 0 on success and 1 on any refusal or failure. Every printed line passes
through the redaction module. The steps themselves live in `retrieval.repository_indexing`, shared
with the retrieval service.
"""

from __future__ import annotations

import argparse
import sys

from pymongo.errors import PyMongoError

from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    EMBEDDINGS_PER_DAY,
    INDEXING_EMBEDDINGS_PER_DAY,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError
from retrieval.embedding_budget import (
    MongoEmbeddingBudget,
    build_document_embedder,
    build_indexing_rate_limiter,
)
from retrieval.fingerprints import VersionFingerprints
from retrieval.github_urls import InvalidRepositoryUrlError, parse_github_url
from retrieval.indexer import IndexingReport
from retrieval.other_languages import describe_counts
from retrieval.redaction import redact
from retrieval.refresh_planning import RefreshPlan
from retrieval.repository_cloner import RepositoryError, clone_repository, refreshing_clone
from retrieval.repository_indexing import (
    NoPythonCodeError,
    TooManyChunksError,
    index_repository_version,
)

BYTES_PER_MEGABYTE = 1024 * 1024

LABEL_WIDTH = 36
MINIMUM_LABEL_GAP = 2

FINGERPRINT_PREFIX_LENGTH = 12


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        reference = parse_github_url(arguments.url)
        database = build_mongo_client()[MONGODB_DATABASE]
        embedder = build_document_embedder(database, build_gemini_client())
        run = index_repository_version(
            reference,
            arguments.version,
            database,
            embedder,
            build_indexing_rate_limiter(),
            clone=refreshing_clone if arguments.refresh else clone_repository,
        )
        usage = MongoEmbeddingBudget(database).usage_today()
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
        TooManyChunksError,
        NoPythonCodeError,
        MissingConfigError,
        PyMongoError,
    ) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    metadata = run.metadata
    collection_statistics = run.collection_statistics
    report_lines = [
        f"Repository  {metadata.repository} at {metadata.version} "
        f"({metadata.commit_id[:12]}, {metadata.license_spdx_id})",
        f"Embedding   {embedder.model_id}, {embedder.dimensions} dimensions, {embedder.task_type}",
        f"Vector index   {VECTOR_INDEX_NAME}: {run.vector_index_change}",
        f"Keyword index  {KEYWORD_INDEX_NAME}: {run.keyword_index_change}",
        "",
        _row("Python files indexed", run.indexed_file_count),
        _row("Paths skipped", run.skipped_path_count),
        _row("Other languages skipped", describe_counts(run.other_languages) or "none"),
        _row("Secret findings redacted", run.secret_finding_count),
        _row("Call sites resolved", f"{run.resolved_call_count} of {run.call_site_count}"),
        _row("Call graph edges", run.call_graph_edge_count),
        _row("Fingerprint root", _describe_fingerprints(run.fingerprints)),
        _row("Index mode", _describe_refresh(run.refresh)),
        "",
        *_report_lines(run.report),
        "",
        f"Embedding budget for {usage.day} (Pacific)",
        _row("Indexing", f"{usage.indexing} of {INDEXING_EMBEDDINGS_PER_DAY}"),
        _row("All embeddings", f"{usage.total} of {EMBEDDINGS_PER_DAY}"),
        "",
        "Chunk collection in Atlas (all indexed versions)",
        _row("Documents", collection_statistics["count"]),
        _row("Data size", _megabytes(collection_statistics["size"])),
        _row("Storage size", _megabytes(collection_statistics["storage_size"])),
        _row("Index size", _megabytes(collection_statistics["index_size"])),
        "",
        "Timing",
        *[_row(stage, f"{milliseconds:.0f} ms") for stage, milliseconds in run.timings.items()],
    ]
    for line in report_lines:
        _print(line)
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="https://github.com/<owner>/<repo>")
    parser.add_argument("--version", required=True, help="tag, branch, or commit ID to index")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ask GitHub where a tag or branch points now, and replace an out-of-date saved clone",
    )
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
        _row("Batch token counts", report.batch_token_count_request_count),
        _row("Tokens sent", report.counted_tokens_sent),
        _row("Truncated chunks", report.truncated_chunk_count),
        _row("Chunks written", report.written_chunk_count),
        _row("Chunks kept as stored", report.kept_chunk_count),
        _row("Call links updated", report.updated_calls_count),
        _row("Stale chunks deleted", report.deleted_stale_chunk_count),
        _row("Rate-limit waiting", f"{report.rate_limit_wait_seconds:.1f} s"),
        _row("Retry waiting", f"{report.retry_wait_seconds:.1f} s"),
    ]


def _describe_fingerprints(fingerprints: VersionFingerprints) -> str:
    return (
        f"{fingerprints.root[:FINGERPRINT_PREFIX_LENGTH]} "
        f"({len(fingerprints.files)} files, {len(fingerprints.folders)} folders)"
    )


def _describe_refresh(refresh: RefreshPlan) -> str:
    comparison = refresh.comparison
    if comparison is None:
        return f"full ({refresh.full_index_reason})"
    return (
        f"incremental ({len(comparison.added)} added, {len(comparison.changed)} changed, "
        f"{len(comparison.removed)} removed, {comparison.unchanged_file_count} unchanged files)"
    )


def _row(label: str, value: object) -> str:
    width = max(LABEL_WIDTH, len(label) + MINIMUM_LABEL_GAP)
    return f"  {label.ljust(width)}{value}"


def _megabytes(size_bytes: int) -> str:
    return f"{size_bytes / BYTES_PER_MEGABYTE:.2f} MB"


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
