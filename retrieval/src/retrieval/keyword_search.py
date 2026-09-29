"""Keyword search over stored chunks with Atlas Search, which scores with BM25.

For each query word, BM25 scores a chunk higher when the word appears in it more often, with
diminishing returns; when the word is rare across the collection; and when the chunk is short.
That suits code: an identifier such as `add_url_rule` appears in few chunks, so a match is strong
evidence, where vector search can blur a name into merely similar code. Atlas uses Lucene's BM25
with its standard settings.

The query is analyzed with the same `code` analyzer as the chunks, so `add_url_rule` matches the
whole identifier and `url rule` matches its parts. A match in the chunk's name or qualified name
counts `NAME_FIELD_BOOST` times as much as one in its text or file path, so a definition outranks
the places that merely call it. That weight is a starting value, not yet tuned by evaluation.

`repository`, `version`, and `is_test_file` go in the compound `filter` clause, which narrows the
matches without changing their scores. The score is unbounded and depends on the query, so it can
be compared only with other scores from the same search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo.collection import Collection

from retrieval.config import DEFAULT_SEARCH_LIMIT, KEYWORD_INDEX_NAME, NAME_FIELD_BOOST
from retrieval.search_results import (
    SearchResult,
    result_projection,
    search_result_from,
    validate_search_limit,
)

KEYWORD_SCORE_SOURCE = "searchScore"

NAME_FIELDS = ["name", "qualified_name"]

BODY_FIELDS = ["text", "file_path"]


@dataclass(frozen=True)
class KeywordSearchOptions:
    limit: int = DEFAULT_SEARCH_LIMIT
    exclude_tests: bool = False

    def __post_init__(self) -> None:
        validate_search_limit(self.limit)


def build_keyword_search_pipeline(
    query: str, repository: str, version: str, options: KeywordSearchOptions
) -> list[dict[str, Any]]:
    filter_clauses: list[dict[str, Any]] = [
        {"equals": {"path": "repository", "value": repository}},
        {"equals": {"path": "version", "value": version}},
    ]
    if options.exclude_tests:
        filter_clauses.append({"equals": {"path": "is_test_file", "value": False}})
    name_clause = {
        "text": {
            "query": query,
            "path": NAME_FIELDS,
            "score": {"boost": {"value": NAME_FIELD_BOOST}},
        }
    }
    body_clause = {"text": {"query": query, "path": BODY_FIELDS}}
    search_stage = {
        "index": KEYWORD_INDEX_NAME,
        "compound": {
            "should": [name_clause, body_clause],
            "minimumShouldMatch": 1,
            "filter": filter_clauses,
        },
    }
    return [
        {"$search": search_stage},
        {"$limit": options.limit},
        {"$project": result_projection(KEYWORD_SCORE_SOURCE)},
    ]


def search_chunks_by_keywords(
    chunks_collection: Collection,
    query: str,
    repository: str,
    version: str,
    options: KeywordSearchOptions,
) -> list[SearchResult]:
    """Return the chunks that best match the query's words by BM25, best first."""
    pipeline = build_keyword_search_pipeline(query, repository, version, options)
    return [search_result_from(document) for document in chunks_collection.aggregate(pipeline)]
