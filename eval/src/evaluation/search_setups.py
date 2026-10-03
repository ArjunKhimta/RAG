"""The search setups compared by the evaluation, each built from the pipeline's own functions.

Each setup turns a question into the sources the answer model would see:
- vector, keyword, hybrid: one search method, its top 5 as they are
- vector + rerank, hybrid + rerank: the method's top 30, reranked by the cross-encoder to 5
- router: the default path, keyword top 5 for a code name, hybrid + rerank otherwise
- router + expand: the router's 5 plus the best 3 callers or callees from the call graph
- vector + expand: vector's 5 plus the best 3 callers or callees, the fair comparison for
  expansion if vector search becomes the default

The two expand setups reuse their base setup's sources rather than searching again, and their
time is the base setup's time plus the expansion's. Times are wall-clock milliseconds for the
whole setup; question embeddings come from the cache, so they leave out a live Gemini embedding
call.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pymongo.collection import Collection
from retrieval.answer_sources import expand_sources, find_sources
from retrieval.config import RERANK_CANDIDATE_COUNT, RERANK_RESULT_COUNT
from retrieval.embedders import QueryEmbedder
from retrieval.hybrid_search import HybridSearchOptions, hybrid_search
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.query_router import hybrid_without_router, route_query
from retrieval.reranking import PairScorer, rerank
from retrieval.search_results import SearchResult
from retrieval.vector_search import SearchOptions, search_chunks

MILLISECONDS_PER_SECOND = 1000


class SearchSetup(StrEnum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"
    VECTOR_RERANK = "vector + rerank"
    HYBRID_RERANK = "hybrid + rerank"
    ROUTER = "router"
    ROUTER_EXPAND = "router + expand"
    VECTOR_EXPAND = "vector + expand"


@dataclass(frozen=True)
class SearchContext:
    chunks_collection: Collection
    embedder: QueryEmbedder
    scorer: PairScorer
    repository: str
    version: str


@dataclass(frozen=True)
class SetupRun:
    sources: list[SearchResult]
    milliseconds: float


def run_all_setups(context: SearchContext, question: str) -> dict[SearchSetup, SetupRun]:
    """Run every setup for one question, in the order the setups are declared."""
    runs = {
        SearchSetup.VECTOR: _timed(_vector_sources, context, question),
        SearchSetup.KEYWORD: _timed(_keyword_sources, context, question),
        SearchSetup.HYBRID: _timed(_hybrid_sources, context, question),
        SearchSetup.VECTOR_RERANK: _timed(_vector_rerank_sources, context, question),
        SearchSetup.HYBRID_RERANK: _timed(_hybrid_rerank_sources, context, question),
        SearchSetup.ROUTER: _timed(_router_sources, context, question),
    }
    runs[SearchSetup.ROUTER_EXPAND] = _expanded_run(context, question, runs[SearchSetup.ROUTER])
    runs[SearchSetup.VECTOR_EXPAND] = _expanded_run(context, question, runs[SearchSetup.VECTOR])
    return runs


def _expanded_run(context: SearchContext, question: str, base_run: SetupRun) -> SetupRun:
    expansion_run = _timed(_expanded_sources, context, question, base_run.sources)
    return SetupRun(
        sources=expansion_run.sources,
        milliseconds=base_run.milliseconds + expansion_run.milliseconds,
    )


def _timed(
    search: Callable[..., list[SearchResult]], context: SearchContext, *arguments: object
) -> SetupRun:
    started = time.perf_counter()
    sources = search(context, *arguments)
    milliseconds = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return SetupRun(sources=sources, milliseconds=milliseconds)


def _vector_sources(context: SearchContext, question: str) -> list[SearchResult]:
    return _vector_candidates(context, question, RERANK_RESULT_COUNT)


def _keyword_sources(context: SearchContext, question: str) -> list[SearchResult]:
    return search_chunks_by_keywords(
        context.chunks_collection,
        question,
        context.repository,
        context.version,
        KeywordSearchOptions(limit=RERANK_RESULT_COUNT),
    )


def _hybrid_sources(context: SearchContext, question: str) -> list[SearchResult]:
    return _hybrid_candidates(context, question, RERANK_RESULT_COUNT)


def _vector_rerank_sources(context: SearchContext, question: str) -> list[SearchResult]:
    candidates = _vector_candidates(context, question, RERANK_CANDIDATE_COUNT)
    return [reranked.result for reranked in rerank(question, candidates, context.scorer)]


def _hybrid_rerank_sources(context: SearchContext, question: str) -> list[SearchResult]:
    found = find_sources(
        context.chunks_collection,
        context.embedder,
        context.scorer,
        question,
        context.repository,
        context.version,
        hybrid_without_router(question),
    )
    return found.sources


def _router_sources(context: SearchContext, question: str) -> list[SearchResult]:
    found = find_sources(
        context.chunks_collection,
        context.embedder,
        context.scorer,
        question,
        context.repository,
        context.version,
        route_query(question),
    )
    return found.sources


def _expanded_sources(
    context: SearchContext, question: str, sources: list[SearchResult]
) -> list[SearchResult]:
    expansion = expand_sources(
        context.chunks_collection,
        context.scorer,
        question,
        sources,
        context.repository,
        context.version,
    )
    return expansion.sources


def _vector_candidates(context: SearchContext, question: str, limit: int) -> list[SearchResult]:
    query_vector = context.embedder.embed_query(question)
    return search_chunks(
        context.chunks_collection,
        query_vector,
        context.repository,
        context.version,
        SearchOptions(limit=limit),
    )


def _hybrid_candidates(context: SearchContext, question: str, limit: int) -> list[SearchResult]:
    outcome = hybrid_search(
        context.chunks_collection,
        context.embedder,
        question,
        context.repository,
        context.version,
        HybridSearchOptions(limit=limit),
    )
    return [fused.result for fused in outcome.results]
