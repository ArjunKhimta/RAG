"""Answering a question about an indexed repository version: the steps shared by the ask script
and the retrieval service.

The work splits into what is built once and what is done for each question. Built once: the
query-embedding cache, and the answer model with its rate limiter, which must be shared so every
question counts against the same per-minute limits; a limiter made per question would never stop
anything. The reranker is loaded on first use and then kept, so a process that only ever uses
vector search never spends the memory on it. For each question: check the version is indexed,
can link its license file, and is searchable, find the sources along the chosen search, optionally
add callers and callees from the call graph, and ask the answer model, timing each stage.

The search is one of three. Vector (the default) uses vector search's top 5 directly; on the
50-question Flask evaluation it led the other setups in both answer runs. Router sends a single
code name to keyword search and anything else to hybrid search with reranking. Hybrid sends every
question to hybrid search with reranking.

Not safe to share between threads: the rate limiter and the cache's counters are plain Python
state, so a caller handles one question at a time per asker.
"""

from __future__ import annotations

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
from retrieval.answer_sources import ExpandedSources, expand_sources, find_sources
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore, require_license_path
from retrieval.config import (
    ANSWER_REQUESTS_PER_MINUTE,
    ANSWER_TOKENS_PER_MINUTE,
    KEYWORD_INDEX_NAME,
    VECTOR_INDEX_NAME,
)
from retrieval.embedders import GeminiQueryEmbedder
from retrieval.graph_expansion import require_call_graph
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.query_router import (
    QueryRoute,
    RouteDecision,
    hybrid_without_router,
    route_query,
    vector_without_router,
)
from retrieval.rate_limiter import RateLimiter
from retrieval.reranker_model import verified_reranker_files
from retrieval.reranking import CrossEncoderScorer, PairScorer
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import require_indexed_version
from retrieval.vector_search import require_searchable_version

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


class QuestionAsker:
    """Answers questions with an embedder, answer model, and reranker shared across questions."""

    def __init__(
        self,
        database: Database,
        embedder: CachingQueryEmbedder,
        answer_model: AnswerModel,
        load_scorer: Callable[[], PairScorer],
    ) -> None:
        self._database = database
        self._embedder = embedder
        self._answer_model = answer_model
        self._load_scorer = load_scorer
        self._scorer: PairScorer | None = None

    def ask(
        self,
        question: str,
        repository: str,
        version: str,
        search: str = VECTOR_SEARCH,
        exclude_tests: bool = False,
        expand: bool = False,
    ) -> AskRun:
        repository_record = MongoChunkStore(self._database).find_repository_record(
            repository, version
        )
        require_indexed_version(repository_record, repository, version)
        require_license_path(repository_record, repository, version)
        if expand:
            require_call_graph(repository_record, repository, version)
        require_searchable_version(repository_record, repository, version, self._embedder)
        chunks_collection = self._database[CHUNKS_COLLECTION]
        require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
        require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
        route = route_for(search, question)
        timings: dict[str, float] = {}
        scorer = None
        if route.route == QueryRoute.HYBRID or expand:
            scorer = self._scorer_timed_into(timings)
        hits_before = self._embedder.hit_count
        misses_before = self._embedder.miss_count
        found = find_sources(
            chunks_collection,
            self._embedder,
            scorer,
            question,
            repository,
            version,
            route,
            exclude_tests=exclude_tests,
        )
        embedding_use = _embedding_use(
            self._embedder.hit_count - hits_before, self._embedder.miss_count - misses_before
        )
        if not found.sources:
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

    def _scorer_timed_into(self, timings: dict[str, float]) -> PairScorer:
        """Load the reranker the first time it is needed and keep it; time only that first load."""
        if self._scorer is None:
            stage_started = time.perf_counter()
            self._scorer = self._load_scorer()
            timings["load reranker"] = _milliseconds_since(stage_started)
        return self._scorer


def build_question_asker(database: Database, gemini_client: genai.Client) -> QuestionAsker:
    """Wire the real embedding cache, Gemini answer model, and local reranker."""
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    embedder = CachingQueryEmbedder(GeminiQueryEmbedder(gemini_client), query_embedding_store)
    answer_model = GeminiAnswerModel(
        gemini_client, RateLimiter(ANSWER_REQUESTS_PER_MINUTE, ANSWER_TOKENS_PER_MINUTE)
    )
    return QuestionAsker(database, embedder, answer_model, load_local_reranker)


def load_local_reranker() -> PairScorer:
    return CrossEncoderScorer(verified_reranker_files())


def route_for(search: str, question: str) -> RouteDecision:
    if search == ROUTER_SEARCH:
        return route_query(question)
    if search == HYBRID_SEARCH:
        return hybrid_without_router(question)
    if search == VECTOR_SEARCH:
        return vector_without_router(question)
    raise ValueError(f"Unknown search {search!r}; use one of {', '.join(SEARCH_CHOICES)}")


def _embedding_use(new_hits: int, new_misses: int) -> EmbeddingUse:
    if new_hits:
        return EmbeddingUse.FROM_CACHE
    if new_misses:
        return EmbeddingUse.EMBEDDED
    return EmbeddingUse.NOT_NEEDED


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND
