"""Search an indexed repository version by vector, BM25 keyword, or hybrid search.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/search_repository.py "How are URL rules registered?" \
        --repository pallets/flask --version 3.1.3

    python retrieval/scripts/search_repository.py add_url_rule --mode keyword \
        --repository pallets/flask --version 3.1.3

Options: `--mode vector` (default), `--mode keyword`, `--mode hybrid`, which fuses both result lists
with Reciprocal Rank Fusion and shows each result's rank in each list, or `--mode auto`, which lets
the query router choose: keyword for a single code name, hybrid otherwise. `--limit N` (default 10),
and `--exclude-tests` to leave out chunks from test files. `--exact`, for vector and hybrid modes,
compares the question with every chunk instead of searching approximately. `--rerank` takes the top
30 of the chosen search, scores each against the question with the local cross-encoder, and shows
the best 5 (or `--limit`, at most 30), each with its rank before reranking; it also reports how many
candidates were cut to fit the model and the process's peak memory before and after reranking. Run
`download_reranker_model.py` once before using it.

Refuses to search a version that has not finished indexing, and in vector and hybrid modes one
that was embedded with a different model. Question embeddings are cached in MongoDB, so only a
question not asked before spends an embedding request from the daily quota; keyword mode spends
none. Prints the license, the search used, each result's score, file, 1-indexed line range, name,
and one line of code, then the time for each stage. Exits 0 on success and 1 on any refusal or
failure. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import resource
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
    RERANK_CANDIDATE_COUNT,
    RERANK_RESULT_COUNT,
    RERANKER_MAX_TOKENS,
    RERANKER_MODEL_REPOSITORY,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError, GeminiQueryEmbedder
from retrieval.hybrid_search import HybridSearchOptions, hybrid_search
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.query_router import QueryRoute, route_query
from retrieval.rank_fusion import FusedResult
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError, verified_reranker_files
from retrieval.reranking import CrossEncoderScorer, QuestionTooLongError, RerankedResult, rerank
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, SearchResult, require_indexed_version
from retrieval.vector_search import SearchOptions, require_searchable_version, search_chunks

VECTOR_MODE = "vector"

KEYWORD_MODE = "keyword"

HYBRID_MODE = "hybrid"

AUTO_MODE = "auto"

MILLISECONDS_PER_SECOND = 1000

COMMIT_ID_DISPLAY_LENGTH = 12

MAX_CODE_LINE_LENGTH = 100

LABEL_WIDTH = 28

BYTES_PER_MEGABYTE = 1_000_000

MACOS_PLATFORM = "darwin"

MACOS_MAXRSS_BYTES_PER_UNIT = 1

LINUX_MAXRSS_BYTES_PER_UNIT = 1024


@dataclass(frozen=True)
class DisplayedResult:
    score: float
    result: SearchResult
    rank_note: str | None = None


@dataclass(frozen=True)
class SearchRun:
    description: str
    results: list[DisplayedResult]
    timings: dict[str, float]
    embedding_status: str | None = None
    reranker_status: str | None = None
    memory: dict[str, float] | None = None


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
        search_limit = RERANK_CANDIDATE_COUNT if arguments.rerank else arguments.limit
        mode = arguments.mode
        keyword_query = arguments.query
        route_note = None
        if mode == AUTO_MODE:
            decision = route_query(arguments.query)
            mode = KEYWORD_MODE if decision.route == QueryRoute.KEYWORD else HYBRID_MODE
            keyword_query = decision.query
            route_note = f"router chose {mode}: {decision.reason}"
        if mode == VECTOR_MODE:
            search_run = _run_vector_search(
                arguments, search_limit, database, chunks_collection, repository_record
            )
        elif mode == HYBRID_MODE:
            search_run = _run_hybrid_search(
                arguments, search_limit, database, chunks_collection, repository_record
            )
        else:
            search_run = _run_keyword_search(
                arguments, keyword_query, search_limit, chunks_collection
            )
        if arguments.rerank:
            search_run = _rerank_search_run(arguments, search_run)
    except (SearchRefusedError, QuestionTooLongError) as error:
        _print(f"Refused: {error}")
        return 1
    except EmbeddingRequestError as error:
        if error.is_daily_quota_exhausted:
            _print("Stopped: the daily embedding quota is used up; try again after the reset.")
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    except (MissingConfigError, PyMongoError, RerankerModelError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    commit_id = repository_record["commit_id"][:COMMIT_ID_DISPLAY_LENGTH]
    header_lines = [
        f"Repository  {arguments.repository} at {arguments.version} "
        f"({commit_id}, {repository_record['license_spdx_id']})",
        f"Query       {arguments.query}",
        f"Search      {search_run.description}",
    ]
    if route_note is not None:
        header_lines.append(f"Route       {route_note}")
    if search_run.embedding_status is not None:
        header_lines.append(f"Embedding   {search_run.embedding_status}")
    if search_run.reranker_status is not None:
        header_lines.append(f"Reranker    {search_run.reranker_status}")
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
    if search_run.memory is not None:
        memory_lines = [
            _row(label, f"{megabytes:.0f} MB") for label, megabytes in search_run.memory.items()
        ]
        report_lines.extend(["", "Peak memory (whole process)", *memory_lines])
    for line in report_lines:
        _print(line)
    return 0


def _run_vector_search(
    arguments: argparse.Namespace,
    limit: int,
    database: Database,
    chunks_collection: Collection,
    repository_record: dict[str, Any],
) -> SearchRun:
    options = SearchOptions(
        limit=limit, exclude_tests=arguments.exclude_tests, exact=arguments.exact
    )
    embedder = _checked_query_embedder(arguments, database, repository_record)
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
        results=[DisplayedResult(result.score, result) for result in results],
        timings=timings,
        embedding_status=_describe_cache_use(embedder),
    )


def _run_hybrid_search(
    arguments: argparse.Namespace,
    limit: int,
    database: Database,
    chunks_collection: Collection,
    repository_record: dict[str, Any],
) -> SearchRun:
    options = HybridSearchOptions(
        limit=limit, exclude_tests=arguments.exclude_tests, exact=arguments.exact
    )
    embedder = _checked_query_embedder(arguments, database, repository_record)
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    outcome = hybrid_search(
        chunks_collection,
        embedder,
        arguments.query,
        arguments.repository,
        arguments.version,
        options,
    )
    vector_method = "exact" if options.exact else "approximate"
    method = (
        f"hybrid, RRF of {vector_method} vector and BM25 keyword "
        f"({options.candidate_depth} candidates each)"
    )
    return SearchRun(
        description=_describe_search(method, options.limit, options.exclude_tests),
        results=[
            DisplayedResult(fused.fused_score, fused.result, _describe_ranks(fused))
            for fused in outcome.results
        ],
        timings=outcome.timings,
        embedding_status=_describe_cache_use(embedder),
    )


def _checked_query_embedder(
    arguments: argparse.Namespace, database: Database, repository_record: dict[str, Any]
) -> CachingQueryEmbedder:
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    embedder = CachingQueryEmbedder(
        GeminiQueryEmbedder(build_gemini_client()), query_embedding_store
    )
    require_searchable_version(repository_record, arguments.repository, arguments.version, embedder)
    return embedder


def _run_keyword_search(
    arguments: argparse.Namespace, query: str, limit: int, chunks_collection: Collection
) -> SearchRun:
    options = KeywordSearchOptions(limit=limit, exclude_tests=arguments.exclude_tests)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    stage_started = time.perf_counter()
    results = search_chunks_by_keywords(
        chunks_collection, query, arguments.repository, arguments.version, options
    )
    timings = {"keyword search": _milliseconds_since(stage_started)}
    return SearchRun(
        description=_describe_search("keyword, BM25", options.limit, options.exclude_tests),
        results=[DisplayedResult(result.score, result) for result in results],
        timings=timings,
    )


def _rerank_search_run(arguments: argparse.Namespace, search_run: SearchRun) -> SearchRun:
    """Rerank every candidate, so the cut count covers all of them, then keep the best few."""
    peak_before_megabytes = _peak_memory_megabytes()
    timings = dict(search_run.timings)
    stage_started = time.perf_counter()
    scorer = CrossEncoderScorer(verified_reranker_files())
    timings["load reranker"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    candidates = [displayed.result for displayed in search_run.results]
    reranked_results = rerank(arguments.query, candidates, scorer, RERANK_CANDIDATE_COUNT)
    timings["rerank"] = _milliseconds_since(stage_started)
    truncated_count = sum(1 for reranked in reranked_results if reranked.was_truncated)
    kept_results = reranked_results[: arguments.limit]
    return SearchRun(
        description=f"{search_run.description}, reranked to top {arguments.limit}",
        results=[
            DisplayedResult(
                reranked.rerank_score,
                reranked.result,
                _describe_rerank(arguments.mode, reranked, search_run.results),
            )
            for reranked in kept_results
        ],
        timings=timings,
        embedding_status=search_run.embedding_status,
        reranker_status=(
            f"{RERANKER_MODEL_REPOSITORY}, {len(candidates)} candidates, "
            f"{truncated_count} cut to {RERANKER_MAX_TOKENS} tokens"
        ),
        memory={
            "before loading reranker": peak_before_megabytes,
            "after reranking": _peak_memory_megabytes(),
        },
    )


def _describe_rerank(
    mode: str, reranked: RerankedResult, candidates: list[DisplayedResult]
) -> str:
    description = f"{mode} #{reranked.original_rank}"
    candidate_note = candidates[reranked.original_rank - 1].rank_note
    if candidate_note:
        description += f": {candidate_note}"
    if reranked.was_truncated:
        description += ", cut to fit"
    return description


def _peak_memory_megabytes() -> float:
    """The process's highest resident memory so far; macOS reports bytes, Linux kilobytes."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == MACOS_PLATFORM:
        return peak * MACOS_MAXRSS_BYTES_PER_UNIT / BYTES_PER_MEGABYTE
    return peak * LINUX_MAXRSS_BYTES_PER_UNIT / BYTES_PER_MEGABYTE


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "query", type=_non_blank_query, help="plain-English question, or keywords such as a name"
    )
    parser.add_argument("--repository", required=True, help="<owner>/<repo>, as indexed")
    parser.add_argument("--version", required=True, help="the indexed tag, branch, or commit ID")
    parser.add_argument(
        "--mode",
        choices=[VECTOR_MODE, KEYWORD_MODE, HYBRID_MODE, AUTO_MODE],
        default=VECTOR_MODE,
        help=f"search method (default {VECTOR_MODE})",
    )
    parser.add_argument(
        "--limit",
        type=_search_limit,
        help=(
            f"number of results, 1 to {MAX_SEARCH_LIMIT} (default {DEFAULT_SEARCH_LIMIT}; "
            f"with --rerank, at most {RERANK_CANDIDATE_COUNT}, default {RERANK_RESULT_COUNT})"
        ),
    )
    parser.add_argument(
        "--exclude-tests", action="store_true", help="leave out chunks from test files"
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="vector and hybrid modes: compare with every chunk instead of approximately",
    )
    parser.add_argument(
        "--rerank",
        action="store_true",
        help=f"rerank the top {RERANK_CANDIDATE_COUNT} with the local cross-encoder",
    )
    arguments = parser.parse_args()
    if arguments.exact and arguments.mode == KEYWORD_MODE:
        parser.error("--exact does not apply to --mode keyword")
    if arguments.exact and arguments.mode == AUTO_MODE:
        parser.error("--exact needs a chosen mode: use --mode vector or --mode hybrid")
    if arguments.limit is None:
        arguments.limit = RERANK_RESULT_COUNT if arguments.rerank else DEFAULT_SEARCH_LIMIT
    if arguments.rerank and arguments.limit > RERANK_CANDIDATE_COUNT:
        parser.error(f"with --rerank, --limit must be at most {RERANK_CANDIDATE_COUNT}")
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


def _describe_ranks(fused: FusedResult) -> str:
    rank_descriptions = [f"{list_name} #{rank}" for list_name, rank in fused.ranks.items()]
    if len(rank_descriptions) == 1:
        return f"{rank_descriptions[0]} only"
    return ", ".join(rank_descriptions)


def _result_lines(displayed_results: list[DisplayedResult]) -> list[str]:
    if not displayed_results:
        return ["  No results."]
    lines: list[str] = []
    for rank, displayed in enumerate(displayed_results, start=1):
        result = displayed.result
        location = f"{result.file_path}:{result.start_line}-{result.end_line}"
        rank_note = f"  ({displayed.rank_note})" if displayed.rank_note else ""
        lines.append(f"{rank:>3}. {displayed.score:.4f}  {location}{rank_note}")
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
