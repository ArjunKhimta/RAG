"""Choose the code an answer is written from: the searched sources, then optionally related code.

Two steps, kept apart so their effect can be compared:
1. `find_sources`: follows a route decision. The default, vector search, uses its top 5 as they
   are: on the 50-question Flask evaluation it led the router and hybrid search with reranking in
   both answer runs, and it needs no reranker. The router's decision is still followed when asked
   for. A code name goes to keyword search, whose top 5 are used as they are: on the 264 names
   defined once in Flask, keyword search ranked the definition first for 98.5% of them, and
   reranking lowered that to 75%, because the cross-encoder, trained on web search, favours tests
   that repeat the name. Anything else goes to hybrid search for the top 30 candidates, and the
   cross-encoder keeps the best 5.
2. `expand_sources`: the call graph lists the direct callers and callees of those 5. The first 30,
   in source rank order, are scored against the question by the same cross-encoder, and the best
   3 are added after the sources, each with a note such as "called by source 2".

The graph proposes and the reranker picks. The graph knows which code is connected, but not which
connection matters for this question; the reranker reads the question with each neighbor. The
cap of 30 keeps reranking time bounded when a source is called from many places. Every stage is
timed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pymongo.collection import Collection

from retrieval.config import (
    GRAPH_NEIGHBOR_CANDIDATE_LIMIT,
    GRAPH_NEIGHBOR_RESULT_COUNT,
    RERANK_CANDIDATE_COUNT,
    RERANK_RESULT_COUNT,
)
from retrieval.embedders import QueryEmbedder
from retrieval.graph_expansion import GraphNeighbor, find_graph_neighbors
from retrieval.hybrid_search import HybridSearchOptions, hybrid_search
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.query_router import QueryRoute, RouteDecision
from retrieval.reranking import PairScorer, rerank
from retrieval.search_results import SearchResult
from retrieval.vector_search import SearchOptions, search_chunks

MILLISECONDS_PER_SECOND = 1000


@dataclass(frozen=True)
class FoundSources:
    sources: list[SearchResult]
    timings: dict[str, float]
    route: RouteDecision


@dataclass(frozen=True)
class KeptNeighbor:
    neighbor: GraphNeighbor
    rerank_score: float
    note: str


@dataclass(frozen=True)
class ExpandedSources:
    """The searched sources followed by the kept neighbors, with one note per source.

    A searched source's note is None; a kept neighbor's says how it relates to its source.
    """

    sources: list[SearchResult]
    related_notes: list[str | None]
    found_neighbor_count: int
    kept_neighbors: list[KeptNeighbor]
    timings: dict[str, float]


def find_sources(
    chunks_collection: Collection,
    embedder: QueryEmbedder,
    scorer: PairScorer | None,
    question: str,
    repository: str,
    version: str,
    route: RouteDecision,
    exclude_tests: bool = False,
) -> FoundSources:
    """Find the answer's sources along `route`. The scorer is needed only for the hybrid route."""
    if route.route == QueryRoute.KEYWORD:
        return _keyword_sources(chunks_collection, route, repository, version, exclude_tests)
    if route.route == QueryRoute.VECTOR:
        return _vector_sources(
            chunks_collection, embedder, route, repository, version, exclude_tests
        )
    if scorer is None:
        raise ValueError("The hybrid route needs the reranker's scorer")
    search_options = HybridSearchOptions(limit=RERANK_CANDIDATE_COUNT, exclude_tests=exclude_tests)
    outcome = hybrid_search(
        chunks_collection, embedder, question, repository, version, search_options
    )
    timings = dict(outcome.timings)
    candidates = [fused.result for fused in outcome.results]
    if not candidates:
        return FoundSources(sources=[], timings=timings, route=route)
    stage_started = time.perf_counter()
    reranked_results = rerank(question, candidates, scorer, RERANK_RESULT_COUNT)
    timings["rerank"] = _milliseconds_since(stage_started)
    sources = [reranked.result for reranked in reranked_results]
    return FoundSources(sources=sources, timings=timings, route=route)


def _vector_sources(
    chunks_collection: Collection,
    embedder: QueryEmbedder,
    route: RouteDecision,
    repository: str,
    version: str,
    exclude_tests: bool,
) -> FoundSources:
    options = SearchOptions(limit=RERANK_RESULT_COUNT, exclude_tests=exclude_tests)
    stage_started = time.perf_counter()
    query_vector = embedder.embed_query(route.query)
    timings = {"embed query": _milliseconds_since(stage_started)}
    stage_started = time.perf_counter()
    sources = search_chunks(chunks_collection, query_vector, repository, version, options)
    timings["vector search"] = _milliseconds_since(stage_started)
    return FoundSources(sources=sources, timings=timings, route=route)


def _keyword_sources(
    chunks_collection: Collection,
    route: RouteDecision,
    repository: str,
    version: str,
    exclude_tests: bool,
) -> FoundSources:
    options = KeywordSearchOptions(limit=RERANK_RESULT_COUNT, exclude_tests=exclude_tests)
    stage_started = time.perf_counter()
    sources = search_chunks_by_keywords(
        chunks_collection, route.query, repository, version, options
    )
    timings = {"keyword search": _milliseconds_since(stage_started)}
    return FoundSources(sources=sources, timings=timings, route=route)


def expand_sources(
    chunks_collection: Collection,
    scorer: PairScorer,
    question: str,
    sources: list[SearchResult],
    repository: str,
    version: str,
    candidate_limit: int = GRAPH_NEIGHBOR_CANDIDATE_LIMIT,
    kept_count: int = GRAPH_NEIGHBOR_RESULT_COUNT,
) -> ExpandedSources:
    timings: dict[str, float] = {}
    stage_started = time.perf_counter()
    neighbors = find_graph_neighbors(chunks_collection, sources, repository, version)
    timings["expand context"] = _milliseconds_since(stage_started)
    candidates = neighbors[:candidate_limit]
    kept_neighbors: list[KeptNeighbor] = []
    if candidates:
        stage_started = time.perf_counter()
        kept_neighbors = _best_neighbors(question, candidates, sources, scorer, kept_count)
        timings["rerank neighbors"] = _milliseconds_since(stage_started)
    return ExpandedSources(
        sources=[*sources, *(kept.neighbor.result for kept in kept_neighbors)],
        related_notes=[*([None] * len(sources)), *(kept.note for kept in kept_neighbors)],
        found_neighbor_count=len(neighbors),
        kept_neighbors=kept_neighbors,
        timings=timings,
    )


def relation_note(neighbor: GraphNeighbor, sources: list[SearchResult]) -> str:
    """Describe a neighbor from its own side, such as "called by source 2"."""
    source_ids = [source.chunk_id for source in sources]
    source_number = source_ids.index(neighbor.source_chunk_id) + 1
    return f"{neighbor.relation} source {source_number}"


def _best_neighbors(
    question: str,
    candidates: list[GraphNeighbor],
    sources: list[SearchResult],
    scorer: PairScorer,
    kept_count: int,
) -> list[KeptNeighbor]:
    limit = min(kept_count, len(candidates))
    candidate_results = [candidate.result for candidate in candidates]
    reranked_results = rerank(question, candidate_results, scorer, limit)
    kept_neighbors: list[KeptNeighbor] = []
    for reranked in reranked_results:
        neighbor = candidates[reranked.original_rank - 1]
        kept_neighbors.append(
            KeptNeighbor(
                neighbor=neighbor,
                rerank_score=reranked.rerank_score,
                note=relation_note(neighbor, sources),
            )
        )
    return kept_neighbors


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
