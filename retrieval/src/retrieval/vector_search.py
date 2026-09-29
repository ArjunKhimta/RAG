"""Vector search over stored chunks with Atlas Vector Search.

Every stored vector and every query vector has length 1, so the index's dot product equals the
cosine similarity. Atlas reports the score as (1 + cosine) / 2, so scores run from 0 to 1.

`repository`, `version`, and `is_test_file` are filter fields of the index, so Atlas filters while
it searches. Filtering afterwards could drop most of the top results and return fewer than asked.

By default the search is approximate: Atlas walks an HNSW graph, explores `numCandidates`
neighbours, and keeps the best `limit`. More candidates finds the true nearest neighbours more
often but takes longer; 20 times the limit follows Atlas's guidance. Exact search compares the
query with every chunk of the version, which is affordable for a repository of a few thousand
chunks and shows how many results the approximate search misses.

Besides the checks every search makes, vector search is refused when the version's vectors came
from a different model or dimension count than the query embedder's: vectors from different
models cannot be compared, and the results would look plausible but mean nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo.collection import Collection

from retrieval.config import (
    DEFAULT_SEARCH_LIMIT,
    VECTOR_INDEX_NAME,
    VECTOR_SEARCH_CANDIDATE_MULTIPLIER,
)
from retrieval.embedders import EmbeddingIdentity
from retrieval.search_indexes import EMBEDDING_FIELD
from retrieval.search_results import (
    SearchRefusedError,
    SearchResult,
    require_indexed_version,
    result_projection,
    search_result_from,
    validate_search_limit,
)

VECTOR_SCORE_SOURCE = "vectorSearchScore"


@dataclass(frozen=True)
class SearchOptions:
    limit: int = DEFAULT_SEARCH_LIMIT
    exclude_tests: bool = False
    exact: bool = False

    def __post_init__(self) -> None:
        validate_search_limit(self.limit)

    @property
    def candidate_count(self) -> int:
        return self.limit * VECTOR_SEARCH_CANDIDATE_MULTIPLIER


def require_searchable_version(
    repository_record: dict[str, Any] | None,
    repository: str,
    version: str,
    query_embedder: EmbeddingIdentity,
) -> None:
    require_indexed_version(repository_record, repository, version)
    stored_model = repository_record.get("embedding_model")
    stored_dimensions = repository_record.get("embedding_dimensions")
    if stored_model != query_embedder.model_id or stored_dimensions != query_embedder.dimensions:
        raise SearchRefusedError(
            f"{repository} at {version} was embedded with {stored_model} at {stored_dimensions} "
            f"dimensions, but queries use {query_embedder.model_id} at "
            f"{query_embedder.dimensions}; vectors from different models cannot be compared"
        )


def build_vector_search_pipeline(
    query_vector: list[float], repository: str, version: str, options: SearchOptions
) -> list[dict[str, Any]]:
    filter_conditions: list[dict[str, Any]] = [
        {"repository": {"$eq": repository}},
        {"version": {"$eq": version}},
    ]
    if options.exclude_tests:
        filter_conditions.append({"is_test_file": {"$eq": False}})
    search_stage: dict[str, Any] = {
        "index": VECTOR_INDEX_NAME,
        "path": EMBEDDING_FIELD,
        "queryVector": query_vector,
        "filter": {"$and": filter_conditions},
        "limit": options.limit,
    }
    if options.exact:
        search_stage["exact"] = True
    else:
        search_stage["numCandidates"] = options.candidate_count
    return [
        {"$vectorSearch": search_stage},
        {"$project": result_projection(VECTOR_SCORE_SOURCE)},
    ]


def search_chunks(
    chunks_collection: Collection,
    query_vector: list[float],
    repository: str,
    version: str,
    options: SearchOptions,
) -> list[SearchResult]:
    """Return the chunks nearest the query vector, best first."""
    pipeline = build_vector_search_pipeline(query_vector, repository, version, options)
    return [search_result_from(document) for document in chunks_collection.aggregate(pipeline)]
