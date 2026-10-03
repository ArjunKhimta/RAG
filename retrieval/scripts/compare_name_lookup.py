"""Check how well each search route finds a definition when asked for it by name.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/compare_name_lookup.py --repository pallets/flask --version 3.1.3

Every function, method, and class name defined exactly once in the repository's source files
becomes a test question: the name alone, which should bring back its own definition. Names
defined more than once, such as `__init__`, are left out because no single answer is right.

Compares three routes:
- keyword: keyword search's top 5 as they are
- keyword + rerank: keyword search's top 30, reranked by the cross-encoder to 5
- hybrid + rerank: today's path, hybrid search's top 30 reranked to 5

The two keyword routes run on every name and cost no Gemini requests. Hybrid needs each name
embedded, so it runs on a fixed random sample (40 by default), one embedding request per name not
asked before. All three are reported on that same sample too, so they compare like for like.
The names are generated, not handwritten, so this checks one component and is not the evaluation.
Exits 0 on success and 1 on a refusal or failure. Every printed line passes through the redaction
module.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.code_graph import symbol_for
from retrieval.config import (
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    RERANK_CANDIDATE_COUNT,
    RERANK_RESULT_COUNT,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import GeminiQueryEmbedder
from retrieval.gemini_errors import GeminiRequestError
from retrieval.github_urls import InvalidRepositoryUrlError, parse_github_url
from retrieval.hybrid_search import HybridSearchOptions, hybrid_search
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.redaction import redact
from retrieval.repository_cloner import RepositoryError, clone_repository
from retrieval.repository_walker import chunk_source_files, find_python_files
from retrieval.reranker_model import RerankerModelError, verified_reranker_files
from retrieval.reranking import CrossEncoderScorer, PairScorer, rerank
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, SearchResult, require_indexed_version
from retrieval.vector_search import require_searchable_version

SAMPLE_SEED = 2026

DEFAULT_SAMPLE_SIZE = 40

MILLISECONDS_PER_SECOND = 1000

PERCENT = 100

TARGET_KINDS = frozenset({ChunkKind.FUNCTION, ChunkKind.METHOD, ChunkKind.CLASS})

KEYWORD_ROUTE = "keyword"

KEYWORD_RERANK_ROUTE = "keyword + rerank"

HYBRID_RERANK_ROUTE = "hybrid + rerank"

GITHUB_URL = "https://github.com/{repository}"


@dataclass(frozen=True)
class NameTarget:
    name: str
    symbol: str


@dataclass
class RouteResults:
    """The rank of each target's definition in one route's 5 results; None when missing."""

    ranks: dict[str, int | None] = field(default_factory=dict)
    milliseconds: list[float] = field(default_factory=list)


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        report_lines = _compare(arguments.repository, arguments.version, arguments.sample_size)
    except (SearchRefusedError, InvalidRepositoryUrlError, RepositoryError) as error:
        _print(f"Refused: {error}")
        return 1
    except (MissingConfigError, PyMongoError, RerankerModelError, GeminiRequestError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    for line in report_lines:
        _print(line)
    return 0


def _compare(repository: str, version: str, sample_size: int) -> list[str]:
    cloned = clone_repository(parse_github_url(GITHUB_URL.format(repository=repository)), version)
    chunks = chunk_source_files(find_python_files(cloned.source_path).files).chunks
    targets = unique_name_targets(chunks)
    database = build_mongo_client()[MONGODB_DATABASE]
    record = MongoChunkStore(database).find_repository_record(repository, version)
    require_indexed_version(record, repository, version)
    if record["commit_id"] != cloned.metadata.commit_id:
        raise SearchRefusedError(
            f"The local clone is at {cloned.metadata.commit_id}, but the index is at "
            f"{record['commit_id']}"
        )
    chunks_collection = database[CHUNKS_COLLECTION]
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    scorer = CrossEncoderScorer(verified_reranker_files())
    keyword_results = RouteResults()
    keyword_rerank_results = RouteResults()
    for target in targets:
        _run_keyword_routes(
            chunks_collection,
            scorer,
            target,
            repository,
            version,
            keyword_results,
            keyword_rerank_results,
        )
    sample = random.Random(SAMPLE_SEED).sample(targets, min(sample_size, len(targets)))
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    query_embedder = GeminiQueryEmbedder(build_gemini_client())
    embedder = CachingQueryEmbedder(query_embedder, query_embedding_store)
    require_searchable_version(record, repository, version, embedder)
    hybrid_results = RouteResults()
    for target in sample:
        started = time.perf_counter()
        results = _hybrid_then_rerank(
            chunks_collection, embedder, scorer, target.name, repository, version
        )
        hybrid_results.milliseconds.append(_milliseconds_since(started))
        hybrid_results.ranks[target.name] = _rank_of(target, results)
    sample_names = [target.name for target in sample]
    return [
        f"Repository  {repository} at {version} ({cloned.metadata.commit_id[:12]})",
        f"Names       {len(targets)} defined exactly once in source files; "
        f"sample of {len(sample)} (seed {SAMPLE_SEED}) for hybrid",
        "Success     the name's own definition is result 1, or within the 5 results",
        "",
        f"All {len(targets)} names",
        _summary_row(KEYWORD_ROUTE, keyword_results, None),
        _summary_row(KEYWORD_RERANK_ROUTE, keyword_rerank_results, None),
        "",
        f"Sample of {len(sample)} names",
        _summary_row(KEYWORD_ROUTE, keyword_results, sample_names),
        _summary_row(KEYWORD_RERANK_ROUTE, keyword_rerank_results, sample_names),
        _summary_row(HYBRID_RERANK_ROUTE, hybrid_results, sample_names),
        "",
        *_change_lines(keyword_results, keyword_rerank_results),
        *_miss_lines(KEYWORD_ROUTE, keyword_results),
        *_miss_lines(KEYWORD_RERANK_ROUTE, keyword_rerank_results),
        *_miss_lines(HYBRID_RERANK_ROUTE, hybrid_results),
        f"Embedding requests: {embedder.miss_count} (cache hits {embedder.hit_count})",
    ]


def unique_name_targets(chunks: list[CodeChunk]) -> list[NameTarget]:
    """Return each definition name that belongs to exactly one symbol in the source files."""
    symbols_by_name: dict[str, set[str]] = defaultdict(set)
    for chunk in chunks:
        if chunk.is_test_file or chunk.kind not in TARGET_KINDS:
            continue
        symbols_by_name[chunk.name].add(symbol_for(chunk.file_path, chunk.qualified_name))
    return [
        NameTarget(name=name, symbol=next(iter(symbols)))
        for name, symbols in sorted(symbols_by_name.items())
        if len(symbols) == 1
    ]


def _run_keyword_routes(
    chunks_collection: Collection,
    scorer: PairScorer,
    target: NameTarget,
    repository: str,
    version: str,
    keyword_results: RouteResults,
    keyword_rerank_results: RouteResults,
) -> None:
    started = time.perf_counter()
    candidates = search_chunks_by_keywords(
        chunks_collection,
        target.name,
        repository,
        version,
        KeywordSearchOptions(limit=RERANK_CANDIDATE_COUNT),
    )
    search_milliseconds = _milliseconds_since(started)
    keyword_results.ranks[target.name] = _rank_of(target, candidates[:RERANK_RESULT_COUNT])
    keyword_results.milliseconds.append(search_milliseconds)
    started = time.perf_counter()
    reranked = [result.result for result in rerank(target.name, candidates, scorer)]
    rerank_milliseconds = _milliseconds_since(started)
    keyword_rerank_results.ranks[target.name] = _rank_of(target, reranked)
    keyword_rerank_results.milliseconds.append(search_milliseconds + rerank_milliseconds)


def _hybrid_then_rerank(
    chunks_collection: Collection,
    embedder: CachingQueryEmbedder,
    scorer: PairScorer,
    name: str,
    repository: str,
    version: str,
) -> list[SearchResult]:
    outcome = hybrid_search(
        chunks_collection,
        embedder,
        name,
        repository,
        version,
        HybridSearchOptions(limit=RERANK_CANDIDATE_COUNT),
    )
    candidates = [fused.result for fused in outcome.results]
    return [result.result for result in rerank(name, candidates, scorer)]


def _rank_of(target: NameTarget, results: list[SearchResult]) -> int | None:
    for rank, result in enumerate(results, start=1):
        if symbol_for(result.file_path, result.qualified_name) == target.symbol:
            return rank
    return None


def _summary_row(label: str, route_results: RouteResults, names: list[str] | None) -> str:
    chosen_names = names if names is not None else list(route_results.ranks)
    ranks = [route_results.ranks[name] for name in chosen_names]
    total = len(ranks)
    first_count = sum(1 for rank in ranks if rank == 1)
    top_five_count = sum(1 for rank in ranks if rank is not None)
    average_milliseconds = sum(route_results.milliseconds) / len(route_results.milliseconds)
    return (
        f"  {label.ljust(18)} first {first_count:3}/{total} ({first_count / total * PERCENT:5.1f}%)"
        f"   top 5 {top_five_count:3}/{total} ({top_five_count / total * PERCENT:5.1f}%)"
        f"   {average_milliseconds:6.0f} ms per name"
    )


def _change_lines(before: RouteResults, after: RouteResults) -> list[str]:
    """List names whose rank the reranker changed, comparing keyword with keyword + rerank."""
    better: list[str] = []
    worse: list[str] = []
    for name, before_rank in before.ranks.items():
        after_rank = after.ranks[name]
        before_value = before_rank or RERANK_RESULT_COUNT + 1
        after_value = after_rank or RERANK_RESULT_COUNT + 1
        if after_value < before_value:
            better.append(f"{name} ({_describe_rank(before_rank)} to {_describe_rank(after_rank)})")
        elif after_value > before_value:
            worse.append(f"{name} ({_describe_rank(before_rank)} to {_describe_rank(after_rank)})")
    return [
        f"Reranking keyword results moved {len(better)} names up and {len(worse)} down",
        f"  up:   {', '.join(better) or 'none'}",
        f"  down: {', '.join(worse) or 'none'}",
        "",
    ]


def _miss_lines(label: str, route_results: RouteResults) -> list[str]:
    missed = [name for name, rank in route_results.ranks.items() if rank is None]
    return [f"Missed the top 5 with {label}: {', '.join(missed) or 'none'}", ""]


def _describe_rank(rank: int | None) -> str:
    return "missing" if rank is None else f"#{rank}"


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", required=True, help="<owner>/<repo>, as indexed")
    parser.add_argument("--version", required=True, help="the indexed tag, branch, or commit ID")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="how many names to run through hybrid search (one embedding request each)",
    )
    return parser.parse_args()


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
