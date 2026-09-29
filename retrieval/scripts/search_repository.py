"""Search an indexed repository version with a plain-English question, using vector search.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/search_repository.py "How are URL rules registered?" \
        --repository pallets/flask --version 3.1.3

Options: `--limit N` (default 10), `--exclude-tests` to leave out chunks from test files, and
`--exact` to compare the query with every chunk instead of searching approximately.

Refuses to search a version that has not finished indexing or was embedded with a different
model. Question embeddings are cached in MongoDB, so only a question not asked before spends an
embedding request from the daily quota. Prints the license, whether the question's embedding came
from the cache, each result's score, file, 1-indexed line range, name, and one line of code, then
the time for each stage. Exits 0 on success and 1 on any refusal or failure. Every printed line
passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time

from pymongo.errors import PyMongoError

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    MONGODB_DATABASE,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError, GeminiQueryEmbedder
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.redaction import redact
from retrieval.vector_search import (
    SearchOptions,
    SearchRefusedError,
    SearchResult,
    require_queryable_index,
    require_searchable_version,
    search_chunks,
)

MILLISECONDS_PER_SECOND = 1000

COMMIT_ID_DISPLAY_LENGTH = 12

MAX_CODE_LINE_LENGTH = 100

LABEL_WIDTH = 28


def main() -> int:
    arguments = _parse_arguments()
    options = SearchOptions(
        limit=arguments.limit, exclude_tests=arguments.exclude_tests, exact=arguments.exact
    )
    load_environment()
    timings: dict[str, float] = {}
    try:
        mongo_client = build_mongo_client()
        database = mongo_client[MONGODB_DATABASE]
        store = MongoChunkStore(database)
        chunks_collection = database[CHUNKS_COLLECTION]
        query_embedding_store = MongoQueryEmbeddingStore(database)
        query_embedding_store.ensure_indexes()
        embedder = CachingQueryEmbedder(
            GeminiQueryEmbedder(build_gemini_client()), query_embedding_store
        )
        repository_record = store.find_repository_record(arguments.repository, arguments.version)
        require_searchable_version(
            repository_record, arguments.repository, arguments.version, embedder
        )
        require_queryable_index(chunks_collection)
        stage_started = time.perf_counter()
        query_vector = embedder.embed_query(arguments.question)
        timings["embed query"] = _milliseconds_since(stage_started)
        stage_started = time.perf_counter()
        results = search_chunks(
            chunks_collection, query_vector, arguments.repository, arguments.version, options
        )
        timings["vector search"] = _milliseconds_since(stage_started)
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
    report_lines = [
        f"Repository  {arguments.repository} at {arguments.version} "
        f"({commit_id}, {repository_record['license_spdx_id']})",
        f"Question    {arguments.question}",
        f"Search      {_describe_search(options)}",
        f"Embedding   {_describe_cache_use(embedder)}",
        "",
        *_result_lines(results),
        "",
        "Timing",
        *[_row(stage, f"{milliseconds:.0f} ms") for stage, milliseconds in timings.items()],
    ]
    for line in report_lines:
        _print(line)
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", type=_non_blank_question, help="plain-English question")
    parser.add_argument("--repository", required=True, help="<owner>/<repo>, as indexed")
    parser.add_argument("--version", required=True, help="the indexed tag, branch, or commit ID")
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
        "--exact", action="store_true", help="compare with every chunk instead of approximately"
    )
    return parser.parse_args()


def _non_blank_question(value: str) -> str:
    question = value.strip()
    if not question:
        raise argparse.ArgumentTypeError("the question must not be blank")
    return question


def _search_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{value!r} is not a whole number") from error
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise argparse.ArgumentTypeError(f"the limit must be between 1 and {MAX_SEARCH_LIMIT}")
    return limit


def _describe_search(options: SearchOptions) -> str:
    if options.exact:
        method = "vector, exact"
    else:
        method = f"vector, approximate ({options.candidate_count} candidates)"
    tests = "test files excluded" if options.exclude_tests else "test files included"
    return f"{method}, top {options.limit}, {tests}"


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
