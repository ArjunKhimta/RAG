"""Hybrid search: vector search and BM25 keyword search, fused with Reciprocal Rank Fusion.

Each search fetches `HYBRID_CANDIDATE_DEPTH` candidates, 30, matching the number the reranker will
take next, or the requested limit if that is larger. A chunk found by only one search still takes
part; it earns from one list instead of two.

The searches run one after another, and each stage is timed separately, so the latency evaluation
can show whether running them in parallel would be worth the added complexity.

The checks that make a search meaningful (a fully indexed version, matching embedding model,
queryable indexes) are the caller's job, as for the individual searches.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pymongo.collection import Collection

from retrieval.config import DEFAULT_SEARCH_LIMIT, HYBRID_CANDIDATE_DEPTH
from retrieval.embedders import QueryEmbedder
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.rank_fusion import FusedResult, reciprocal_rank_fusion
from retrieval.search_results import validate_search_limit
from retrieval.vector_search import SearchOptions, search_chunks

VECTOR_LIST = "vector"

KEYWORD_LIST = "keyword"

MILLISECONDS_PER_SECOND = 1000


@dataclass(frozen=True)
class HybridSearchOptions:
    limit: int = DEFAULT_SEARCH_LIMIT
    exclude_tests: bool = False
    exact: bool = False

    def __post_init__(self) -> None:
        validate_search_limit(self.limit)

    @property
    def candidate_depth(self) -> int:
        return max(HYBRID_CANDIDATE_DEPTH, self.limit)


@dataclass(frozen=True)
class HybridSearchOutcome:
    results: list[FusedResult]
    timings: dict[str, float]


def hybrid_search(
    chunks_collection: Collection,
    query_embedder: QueryEmbedder,
    question: str,
    repository: str,
    version: str,
    options: HybridSearchOptions,
) -> HybridSearchOutcome:
    vector_options = SearchOptions(
        limit=options.candidate_depth, exclude_tests=options.exclude_tests, exact=options.exact
    )
    keyword_options = KeywordSearchOptions(
        limit=options.candidate_depth, exclude_tests=options.exclude_tests
    )
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    query_vector = query_embedder.embed_query(question)
    timings["embed query"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    vector_results = search_chunks(
        chunks_collection, query_vector, repository, version, vector_options
    )
    timings["vector search"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    keyword_results = search_chunks_by_keywords(
        chunks_collection, question, repository, version, keyword_options
    )
    timings["keyword search"] = _milliseconds_since(stage_started)
    stage_started = time.perf_counter()
    fused_results = reciprocal_rank_fusion(
        {VECTOR_LIST: vector_results, KEYWORD_LIST: keyword_results}, options.limit
    )
    timings["rank fusion"] = _milliseconds_since(stage_started)
    return HybridSearchOutcome(results=fused_results, timings=timings)


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
