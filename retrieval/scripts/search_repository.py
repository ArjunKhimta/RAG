"""Search an indexed repository version by vector search or by BM25 keyword search.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/search_repository.py "How are URL rules registered?" \
        --repository pallets/flask --version 3.1.3

    python retrieval/scripts/search_repository.py add_url_rule --mode keyword \
        --repository pallets/flask --version 3.1.3

Options: `--mode vector` (default) or `--mode keyword`, `--limit N` (default 10), and
`--exclude-tests` to leave out chunks from test files. `--exact`, for vector mode only, compares
the question with every chunk instead of searching approximately.

Refuses to search a version that has not finished indexing, and in vector mode one that was
embedded with a different model. Question embeddings are cached in MongoDB, so only a question
not asked before spends an embedding request from the daily quota; keyword mode spends none.
Prints the license, the search used, each result's score, file, 1-indexed line range, name, and one
line of code, then the time for each stage. Exits 0 on success and 1 on any refusal or failure.
Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Any

from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    DEFAULT_SEARCH_LIMIT,
    KEYWORD_INDEX_NAME,
    MAX_SEARCH_LIMIT,
    MONGODB_DATABASE,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError, GeminiQueryEmbedder
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.redaction import redact
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, SearchResult, require_indexed_version
from retrieval.vector_search import SearchOptions, require_searchable_version, search_chunks

VECTOR_MODE = "vector"

KEYWORD_MODE = "keyword"

MILLISECONDS_PER_SECOND = 1000

COMMIT_ID_DISPLAY_LENGTH = 12

MAX_CODE_LINE_LENGTH = 100

LABEL_WIDTH = 28


@dataclass(frozen=True)
class SearchRun:
    description: str
    results: list[SearchResult]
    timings: dict[str, float]
    embedding_status: str | None = None


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        mongo_client = build_mongo_client()
        database = mongo_client[MONGODB_DATABASE]
        chunks_collection = database[CHUNKS_COLLECTION]
        store = MongoChunkStore(database)
        repository_record = store.find_repository_record(arguments.repository, arguments.version)
        require_indexed_version(repository_record, arguments.repository, arguments.version)
        if arguments.mode == VECTOR_MODE:
            search_run = _run_vector_search(
                arguments, database, chunks_collection, repository_record
            )
        else:
            search_run = _run_keyword_search(arguments, chunks_collection)
    except SearchRefusedError as error:
        _print(f"Refused: {error}")
        return 1
    except EmbeddingRequestError as error:
        if error.is_daily_quota_exhausted:
            _print("Stopped: the daily embedding quota is used up; try again after the reset.")
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    except (MissingConfigError, PyMongoError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    commit_id = repository_record["commit_id"][:COMMIT_ID_DISPLAY_LENGTH]
    header_lines = [
        f"Repository  {arguments.repository} at {arguments.version} "
        f"({commit_id}, {repository_record['license_spdx_id']})",
        f"Query       {arguments.query}",
        f"Search      {search_run.description}",
    ]
    if search_run.embedding_status is not None:
        header_lines.append(f"Embedding   {search_run.embedding_status}")
    timing_lines = [
        _row(stage, f"{milliseconds:.0f} ms") for stage, milliseconds in search_run.timings.items()
    ]
    report_lines = [
        *header_lines,
        "",
        *_result_lines(search_run.results),
        "",
        "Timing",
        *timing_lines,
    ]
    for line in report_lines:
        _print(line)
    return 0


def _run_vector_search(
    arguments: argparse.Namespace,
    database: Database,
    chunks_collection: Collection,
    repository_record: dict[str, Any],
) -> SearchRun:
    options = SearchOptions(
        limit=arguments.limit, exclude_tests=arguments.exclude_tests, exact=arguments.exact
    )
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    embedder = CachingQueryEmbedder(
        GeminiQueryEmbedder(build_gemini_client()), query_embedding_store
    )
    require_searchable_version(repository_record, arguments.repository, arguments.version, embedder)
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    query_vector = embedder.embed_query(arguments.query)
    timings["embed query"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    results = search_chunks(
        chunks_collection, query_vector, arguments.repository, arguments.version, options
    )
    timings["vector search"] = _milliseconds_since(stage_started)
    if options.exact:
        method = "vector, exact"
    else:
        method = f"vector, approximate ({options.candidate_count} candidates)"
    return SearchRun(
        description=_describe_search(method, options.limit, options.exclude_tests),
        results=results,
        timings=timings,
        embedding_status=_describe_cache_use(embedder),
    )


def _run_keyword_search(arguments: argparse.Namespace, chunks_collection: Collection) -> SearchRun:
    options = KeywordSearchOptions(limit=arguments.limit, exclude_tests=arguments.exclude_tests)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    stage_started = time.perf_counter()
    results = search_chunks_by_keywords(
        chunks_collection, arguments.query, arguments.repository, arguments.version, options
    )
    timings = {"keyword search": _milliseconds_since(stage_started)}
    return SearchRun(
        description=_describe_search("keyword, BM25", options.limit, options.exclude_tests),
        results=results,
        timings=timings,
    )


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "query", type=_non_blank_query, help="plain-English question, or keywords such as a name"
    )
    parser.add_argument("--repository", required=True, help="<owner>/<repo>, as indexed")
    parser.add_argument("--version", required=True, help="the indexed tag, branch, or commit ID")
    parser.add_argument(
        "--mode",
        choices=[VECTOR_MODE, KEYWORD_MODE],
        default=VECTOR_MODE,
        help=f"search method (default {VECTOR_MODE})",
    )
    parser.add_argument(
        "--limit",
        type=_search_limit,
        default=DEFAULT_SEARCH_LIMIT,
        help=f"number of results, 1 to {MAX_SEARCH_LIMIT} (default {DEFAULT_SEARCH_LIMIT})",
    )
    parser.add_argument(
        "--exclude-tests", action="store_true", help="leave out chunks from test files"
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="vector mode only: compare with every chunk instead of approximately",
    )
    arguments = parser.parse_args()
    if arguments.exact and arguments.mode != VECTOR_MODE:
        parser.error("--exact applies only to --mode vector")
    return arguments


def _non_blank_query(value: str) -> str:
    query = value.strip()
    if not query:
        raise argparse.ArgumentTypeError("the query must not be blank")
    return query


def _search_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from error
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise argparse.ArgumentTypeError(f"the limit must be between 1 and {MAX_SEARCH_LIMIT}")
    return limit


def _describe_search(method: str, limit: int, exclude_tests: bool) -> str:
    tests = "test files excluded" if exclude_tests else "test files included"
    return f"{method}, top {limit}, {tests}"


def _describe_cache_use(embedder: CachingQueryEmbedder) -> str:
    if embedder.hit_count:
        return "question embedding from the cache (0 requests)"
    return "question embedded and cached (1 request)"


def _result_lines(results: list[SearchResult]) -> list[str]:
    if not results:
        return ["  No results."]
    lines: list[str] = []
    for rank, result in enumerate(results, start=1):
        location = f"{result.file_path}:{result.start_line}-{result.end_line}"
        lines.append(f"{rank:>3}. {result.score:.4f}  {location}")
        lines.append(f"      {_describe_definition(result)}")
        lines.append(f"      {_first_code_line(result)}")
    return lines


def _describe_definition(result: SearchResult) -> str:
    description = f"{result.kind} {result.qualified_name}"
    if result.part_count > 1:
        description += f" (part {result.part_number} of {result.part_count})"
    if result.is_test_file:
        description += " [test]"
    return description


def _first_code_line(result: SearchResult) -> str:
    """Show the signature for later parts of a split definition, whose text lacks it."""
    candidate_lines = [result.signature or "", *result.text.splitlines()]
    first_line = next((line.strip() for line in candidate_lines if line.strip()), "")
    if len(first_line) > MAX_CODE_LINE_LENGTH:
        return first_line[: MAX_CODE_LINE_LENGTH - 3] + "..."
    return first_line


def _row(label: str, value: object) -> str:
    return f"  {label.ljust(LABEL_WIDTH)}{value}"


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
