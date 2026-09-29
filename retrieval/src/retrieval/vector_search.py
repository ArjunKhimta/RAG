"""Vector search over stored chunks with Atlas Vector Search.

The index covers `chunks.embedding` at 768 dimensions with `dotProduct` similarity. Every stored
vector and every query vector has length 1, so the dot product equals the cosine similarity and
skips computing lengths. Atlas reports the score as (1 + cosine) / 2, so scores run from 0 to 1.

`repository`, `version`, and `is_test_file` are declared as filter fields, so Atlas filters while
it searches. Filtering afterwards could drop most of the top results and return fewer than asked.

By default the search is approximate: Atlas walks an HNSW graph, explores `numCandidates`
neighbours, and keeps the best `limit`. More candidates finds the true nearest neighbours more
often but takes longer; 20 times the limit follows Atlas's guidance. Exact search compares the
query with every chunk of the version, which is affordable for a repository of a few thousand
chunks and shows how many results the approximate search misses.

Search is refused, rather than returning misleading results, when the version has no
`repositories` record (indexing never finished), when its vectors came from a different model or
dimension count than the query embedder's, or when the index is still building.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

from retrieval.config import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    VECTOR_INDEX_NAME,
    VECTOR_SEARCH_CANDIDATE_MULTIPLIER,
)
from retrieval.embedders import QueryEmbedder

VECTOR_SEARCH_INDEX_TYPE = "vectorSearch"

EMBEDDING_FIELD = "embedding"

SIMILARITY = "dotProduct"

FILTER_FIELDS = ("repository", "version", "is_test_file")

RESULT_FIELDS = (
    "file_path",
    "start_line",
    "end_line",
    "kind",
    "qualified_name",
    "signature",
    "part_number",
    "part_count",
    "is_test_file",
    "text",
)


class SearchRefusedError(RuntimeError):
    """Raised when searching would give missing or meaningless results."""


class VectorIndexChange(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class SearchOptions:
    limit: int = DEFAULT_SEARCH_LIMIT
    exclude_tests: bool = False
    exact: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"The search limit must be between 1 and {MAX_SEARCH_LIMIT}")

    @property
    def candidate_count(self) -> int:
        return self.limit * VECTOR_SEARCH_CANDIDATE_MULTIPLIER


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    kind: str
    qualified_name: str
    signature: str | None
    part_number: int
    part_count: int
    is_test_file: bool
    text: str
    score: float


def vector_index_definition(dimensions: int) -> dict[str, Any]:
    vector_field = {
        "type": "vector",
        "path": EMBEDDING_FIELD,
        "numDimensions": dimensions,
        "similarity": SIMILARITY,
    }
    filter_fields = [{"type": "filter", "path": field_path} for field_path in FILTER_FIELDS]
    return {"fields": [vector_field, *filter_fields]}


def ensure_vector_index(chunks_collection: Collection, dimensions: int) -> VectorIndexChange:
    """Create the index if missing, update it if its fields differ, and otherwise leave it alone.

    Updating rebuilds the index, so the comparison looks only at the fields set here and ignores
    any defaults Atlas adds to the stored definition.
    """
    wanted_definition = vector_index_definition(dimensions)
    existing_index = _find_vector_index(chunks_collection)
    if existing_index is None:
        index_model = SearchIndexModel(
            definition=wanted_definition, name=VECTOR_INDEX_NAME, type=VECTOR_SEARCH_INDEX_TYPE
        )
        chunks_collection.create_search_index(index_model)
        return VectorIndexChange.CREATED
    existing_definition = existing_index.get("latestDefinition", {})
    if _describe_fields(existing_definition) == _describe_fields(wanted_definition):
        return VectorIndexChange.UNCHANGED
    chunks_collection.update_search_index(VECTOR_INDEX_NAME, wanted_definition)
    return VectorIndexChange.UPDATED


def require_queryable_index(chunks_collection: Collection) -> None:
    existing_index = _find_vector_index(chunks_collection)
    if existing_index is None:
        raise SearchRefusedError(
            f"The vector index {VECTOR_INDEX_NAME} does not exist; run index_repository.py first"
        )
    if not existing_index.get("queryable", False):
        status = existing_index.get("status", "unknown")
        raise SearchRefusedError(
            f"The vector index {VECTOR_INDEX_NAME} is still building (status {status}); "
            "try again in a minute"
        )


def require_searchable_version(
    repository_record: dict[str, Any] | None,
    repository: str,
    version: str,
    query_embedder: QueryEmbedder,
) -> None:
    if repository_record is None:
        raise SearchRefusedError(
            f"{repository} at {version} has not finished indexing; "
            "run index_repository.py until it completes"
        )
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
    projected_fields: dict[str, Any] = {field_name: 1 for field_name in RESULT_FIELDS}
    projected_fields["score"] = {"$meta": "vectorSearchScore"}
    return [{"$vectorSearch": search_stage}, {"$project": projected_fields}]


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


def search_result_from(document: dict[str, Any]) -> SearchResult:
    return SearchResult(
        chunk_id=document["_id"],
        file_path=document["file_path"],
        start_line=document["start_line"],
        end_line=document["end_line"],
        kind=document["kind"],
        qualified_name=document["qualified_name"],
        signature=document.get("signature"),
        part_number=document.get("part_number", 1),
        part_count=document.get("part_count", 1),
        is_test_file=bool(document.get("is_test_file", False)),
        text=document["text"],
        score=float(document["score"]),
    )


def _find_vector_index(chunks_collection: Collection) -> dict[str, Any] | None:
    matching_indexes = list(chunks_collection.list_search_indexes(VECTOR_INDEX_NAME))
    if not matching_indexes:
        return None
    return matching_indexes[0]


def _describe_fields(definition: dict[str, Any]) -> list[tuple[Any, ...]]:
    described_fields = [
        (
            field.get("type"),
            field.get("path"),
            field.get("numDimensions"),
            field.get("similarity"),
        )
        for field in definition.get("fields", [])
    ]
    return sorted(described_fields, key=repr)
