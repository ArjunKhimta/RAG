"""The search setups compared by the evaluation, each built from the pipeline's own functions.

Each setup turns a question into the sources the answer model would see:
- vector, keyword, hybrid: one search method, its top 5 as they are; vector is the default path
  for answers, run through the same `find_sources` call as `ask_repository.py`
- vector + rerank, hybrid + rerank: the method's top 30, reranked by the cross-encoder to 5
- router: the previous default path, keyword top 5 for a code name, hybrid + rerank otherwise
- router + expand: the router's 5 plus the best 3 callers or callees from the call graph
- vector + expand: vector's 5 plus the best 3 callers or callees, the fair comparison for
  expansion now that vector search is the default

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
from retrieval.query_router import hybrid_without_router, route_query, vector_without_router
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
    """A setup's sources and time. `related_notes` is set only by the expand setups."""

    sources: list[SearchResult]
    milliseconds: float
    related_notes: list[str | None] | None = None


def run_all_setups(context: SearchContext, question: str) -> dict[SearchSetup, SetupRun]:
    """Run every setup for one question, in the order the setups are declared."""
    return run_setups(context, question, list(SearchSetup))


def run_setups(
    context: SearchContext, question: str, setups: list[SearchSetup]
) -> dict[SearchSetup, SetupRun]:
    """Run the given setups for one question; an expand setup reuses its base run if present."""
    runs: dict[SearchSetup, SetupRun] = {}
    for setup in setups:
        runs[setup] = _run_setup(context, question, setup, runs)
    return runs


def _run_setup(
    context: SearchContext,
    question: str,
    setup: SearchSetup,
    earlier_runs: dict[SearchSetup, SetupRun],
) -> SetupRun:
    base_setup = EXPANDED_FROM.get(setup)
    if base_setup is None:
        return _timed(BASE_SEARCHES[setup], context, question)
    base_run = earlier_runs.get(base_setup)
    if base_run is None:
        base_run = _timed(BASE_SEARCHES[base_setup], context, question)
    return _expanded_run(context, question, base_run)


def _expanded_run(context: SearchContext, question: str, base_run: SetupRun) -> SetupRun:
    started = time.perf_counter()
    expansion = expand_sources(
        context.chunks_collection,
        context.scorer,
        question,
        base_run.sources,
        context.repository,
        context.version,
    )
    expansion_milliseconds = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return SetupRun(
        sources=expansion.sources,
        milliseconds=base_run.milliseconds + expansion_milliseconds,
        related_notes=expansion.related_notes,
    )


def _timed(
    search: Callable[..., list[SearchResult]], context: SearchContext, *arguments: object
) -> SetupRun:
    started = time.perf_counter()
    sources = search(context, *arguments)
    milliseconds = (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
    return SetupRun(sources=sources, milliseconds=milliseconds)


def _vector_sources(context: SearchContext, question: str) -> list[SearchResult]:
    found = find_sources(
        context.chunks_collection,
        context.embedder,
        context.scorer,
        question,
        context.repository,
        context.version,
        vector_without_router(question),
    )
    return found.sources


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


BASE_SEARCHES: dict[SearchSetup, Callable[[SearchContext, str], list[SearchResult]]] = {
    SearchSetup.VECTOR: _vector_sources,
    SearchSetup.KEYWORD: _keyword_sources,
    SearchSetup.HYBRID: _hybrid_sources,
    SearchSetup.VECTOR_RERANK: _vector_rerank_sources,
    SearchSetup.HYBRID_RERANK: _hybrid_rerank_sources,
    SearchSetup.ROUTER: _router_sources,
}

EXPANDED_FROM = {
    SearchSetup.ROUTER_EXPAND: SearchSetup.ROUTER,
    SearchSetup.VECTOR_EXPAND: SearchSetup.VECTOR,
}
