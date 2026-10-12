"""Answering a question about an indexed repository version: the steps shared by the ask script
and the retrieval service.

The work splits into what is built once and what is done for each question. Built once: the
query-embedding cache, and the answer model with its rate limiter, which must be shared so every
question counts against the same per-minute limits; a limiter made per question would never stop
anything. The reranker is loaded on first use and then kept, so a process that only ever uses
vector search never spends the memory on it. For each question: check the version is indexed,
can link its license file, and is searchable, check the search indexes the chosen search uses,
find the sources along it, optionally add callers and callees from the call graph, and ask the
answer model, timing each stage. The checks are timed together as `checks`. Index readiness is
remembered for a few minutes and re-checked whenever a search finds nothing (see
`search_indexes.IndexReadiness`).

`search` is the same lookup without an answer: the same version checks, then vector search for a
chosen number of results. It never loads the reranker and never asks the answer model, so it
uses at most one embedding request and none of the answer model's daily quota.

The search is one of three. Vector (the default) uses vector search's top 5 directly; on the
50-question Flask evaluation it led the other setups in both answer runs. Router sends a single
code name to keyword search and anything else to hybrid search with reranking. Hybrid sends every
question to hybrid search with reranking.

Safe to share between threads: the service answers searches while a question is being answered.
The rate limiters lock their own state, each request counts its own embedding use through
`CachingQueryEmbedder.for_one_request()` rather than reading shared counters, the reranker is
loaded under a lock so two threads never load it twice, and the database and Gemini clients are
thread-safe. Index readiness needs no lock: each remembered answer is one dictionary read or
write, and the worst overlap is two threads both asking Atlas once.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from google import genai
from pymongo.database import Database

from retrieval.answer_generation import (
    AnswerModel,
    GeminiAnswerModel,
    GeneratedAnswer,
    generate_answer,
)
from retrieval.answer_sources import (
    ExpandedSources,
    expand_sources,
    find_sources,
    find_vector_results,
)
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore, require_license_path
from retrieval.config import (
    ANSWER_REQUESTS_PER_MINUTE,
    ANSWER_TOKENS_PER_MINUTE,
    DEFAULT_SEARCH_LIMIT,
    KEYWORD_INDEX_NAME,
    VECTOR_INDEX_NAME,
)
from retrieval.embedding_budget import build_query_embedder
from retrieval.graph_expansion import require_call_graph
from retrieval.query_cache import CachingQueryEmbedder
from retrieval.query_router import (
    QueryRoute,
    RouteDecision,
    hybrid_without_router,
    route_query,
    vector_without_router,
)
from retrieval.rate_limiter import RateLimiter
from retrieval.reranker_model import RerankerLibrariesMissingError, verified_reranker_files
from retrieval.reranking import PairScorer
from retrieval.search_indexes import IndexReadiness
from retrieval.search_results import SearchResult, require_indexed_version
from retrieval.vector_search import SearchOptions, require_searchable_version

MILLISECONDS_PER_SECOND = 1000

VECTOR_SEARCH = "vector"

ROUTER_SEARCH = "router"

HYBRID_SEARCH = "hybrid"

SEARCH_CHOICES = [VECTOR_SEARCH, ROUTER_SEARCH, HYBRID_SEARCH]


class NoSourcesFoundError(Exception):
    """Raised when the search returns nothing to answer from; keeps the version's record."""

    def __init__(self, repository_record: dict[str, Any]) -> None:
        super().__init__("No code matched the question")
        self.repository_record = repository_record


class EmbeddingUse(StrEnum):
    FROM_CACHE = "from the cache"
    EMBEDDED = "embedded and cached"
    NOT_NEEDED = "not needed"


@dataclass(frozen=True)
class AskRun:
    generated: GeneratedAnswer
    timings: dict[str, float]
    embedding_use: EmbeddingUse
    expansion: ExpandedSources | None
    route: RouteDecision
    repository_record: dict[str, Any]


@dataclass(frozen=True)
class SearchRun:
    results: list[SearchResult]
    timings: dict[str, float]
    embedding_use: EmbeddingUse
    repository_record: dict[str, Any]


class QuestionAsker:
    """Answers questions with an embedder, answer model, and reranker shared across questions."""

    def __init__(
        self,
        database: Database,
        embedder: CachingQueryEmbedder,
        answer_model: AnswerModel,
        load_scorer: Callable[[], PairScorer],
        index_readiness: IndexReadiness | None = None,
    ) -> None:
        self._database = database
        self._index_readiness = index_readiness or IndexReadiness(database[CHUNKS_COLLECTION])
        self._embedder = embedder
        self._answer_model = answer_model
        self._load_scorer = load_scorer
        self._scorer: PairScorer | None = None
        self._scorer_lock = threading.Lock()

    def ask(
        self,
        question: str,
        repository: str,
        version: str,
        search: str = VECTOR_SEARCH,
        exclude_tests: bool = False,
        expand: bool = False,
    ) -> AskRun:
        checks_started = time.perf_counter()
        repository_record = self._searchable_record(repository, version)
        if expand:
            require_call_graph(repository_record, repository, version)
        route = route_for(search, question)
        index_names = indexes_for(route.route)
        for index_name in index_names:
            self._index_readiness.require_queryable(index_name)
        timings = {"checks": _milliseconds_since(checks_started)}
        chunks_collection = self._database[CHUNKS_COLLECTION]
        scorer = None
        if route.route == QueryRoute.HYBRID or expand:
            scorer = self._scorer_timed_into(timings)
        request_embedder = self._embedder.for_one_request()
        found = find_sources(
            chunks_collection,
            request_embedder,
            scorer,
            question,
            repository,
            version,
            route,
            exclude_tests=exclude_tests,
        )
        embedding_use = _embedding_use(request_embedder.hit_count, request_embedder.miss_count)
        if not found.sources:
            for index_name in index_names:
                self._index_readiness.recheck(index_name)
            raise NoSourcesFoundError(repository_record)
        timings.update(found.timings)
        sources = found.sources
        related_notes: list[str | None] | None = None
        expansion = None
        if expand and scorer is not None:
            expansion = expand_sources(
                chunks_collection, scorer, question, found.sources, repository, version
            )
            timings.update(expansion.timings)
            sources = expansion.sources
            related_notes = expansion.related_notes
        stage_started = time.perf_counter()
        generated = generate_answer(question, sources, self._answer_model, related_notes)
        timings["generate answer"] = _milliseconds_since(stage_started)
        return AskRun(
            generated=generated,
            timings=timings,
            embedding_use=embedding_use,
            expansion=expansion,
            route=found.route,
            repository_record=repository_record,
        )

    def search(
        self,
        query: str,
        repository: str,
        version: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        exclude_tests: bool = False,
    ) -> SearchRun:
        options = SearchOptions(limit=limit, exclude_tests=exclude_tests)
        checks_started = time.perf_counter()
        repository_record = self._searchable_record(repository, version)
        self._index_readiness.require_queryable(VECTOR_INDEX_NAME)
        checks_milliseconds = _milliseconds_since(checks_started)
        chunks_collection = self._database[CHUNKS_COLLECTION]
        request_embedder = self._embedder.for_one_request()
        results, search_timings = find_vector_results(
            chunks_collection, request_embedder, query, repository, version, options
        )
        if not results:
            recheck_started = time.perf_counter()
            self._index_readiness.recheck(VECTOR_INDEX_NAME)
            checks_milliseconds += _milliseconds_since(recheck_started)
        return SearchRun(
            results=results,
            timings={"checks": checks_milliseconds, **search_timings},
            embedding_use=_embedding_use(request_embedder.hit_count, request_embedder.miss_count),
            repository_record=repository_record,
        )

    def _searchable_record(self, repository: str, version: str) -> dict[str, Any]:
        """The version's record, once it is indexed, links its license, and matches the embedder."""
        repository_record = MongoChunkStore(self._database).find_repository_record(
            repository, version
        )
        require_indexed_version(repository_record, repository, version)
        require_license_path(repository_record, repository, version)
        require_searchable_version(repository_record, repository, version, self._embedder)
        return repository_record

    def _scorer_timed_into(self, timings: dict[str, float]) -> PairScorer:
        """Load the reranker the first time it is needed and keep it; time only that first load."""
        with self._scorer_lock:
            if self._scorer is None:
                stage_started = time.perf_counter()
                self._scorer = self._load_scorer()
                timings["load reranker"] = _milliseconds_since(stage_started)
            return self._scorer


def build_question_asker(
    database: Database,
    gemini_client: genai.Client,
    max_rate_limit_wait_seconds: float | None = None,
) -> QuestionAsker:
    """Wire the real embedding cache and budget, Gemini answer model, and local reranker. With
    `max_rate_limit_wait_seconds`, a question that would wait longer than that for a per-minute
    slot is refused at once (the service); without it, it waits (the scripts)."""
    embedder = build_query_embedder(database, gemini_client, max_rate_limit_wait_seconds)
    answer_model = GeminiAnswerModel(
        gemini_client,
        RateLimiter(
            ANSWER_REQUESTS_PER_MINUTE,
            ANSWER_TOKENS_PER_MINUTE,
            max_wait_seconds=max_rate_limit_wait_seconds,
        ),
    )
    return QuestionAsker(database, embedder, answer_model, load_local_reranker)


def load_local_reranker() -> PairScorer:
    """Build the cross-encoder, importing its libraries only now.

    The service's image leaves out ONNX Runtime, tokenizers, and numpy because the service never
    reranks, so `retrieval.cross_encoder` must not be imported when this module is.
    """
    try:
        from retrieval.cross_encoder import CrossEncoderScorer
    except ImportError as error:
        raise RerankerLibrariesMissingError(
            "The reranker needs ONNX Runtime, tokenizers, and numpy; install "
            "retrieval/requirements-reranker.txt"
        ) from error
    return CrossEncoderScorer(verified_reranker_files())


def route_for(search: str, question: str) -> RouteDecision:
    if search == ROUTER_SEARCH:
        return route_query(question)
    if search == HYBRID_SEARCH:
        return hybrid_without_router(question)
    if search == VECTOR_SEARCH:
        return vector_without_router(question)
    raise ValueError(f"Unknown search {search!r}; use one of {', '.join(SEARCH_CHOICES)}")


def indexes_for(route: QueryRoute) -> list[str]:
    """The search indexes a route searches: vector, keyword, or both for hybrid."""
    if route == QueryRoute.VECTOR:
        return [VECTOR_INDEX_NAME]
    if route == QueryRoute.KEYWORD:
        return [KEYWORD_INDEX_NAME]
    return [VECTOR_INDEX_NAME, KEYWORD_INDEX_NAME]


def _embedding_use(new_hits: int, new_misses: int) -> EmbeddingUse:
    if new_hits:
        return EmbeddingUse.FROM_CACHE
    if new_misses:
        return EmbeddingUse.EMBEDDED
    return EmbeddingUse.NOT_NEEDED


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
