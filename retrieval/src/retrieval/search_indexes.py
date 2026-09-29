"""The two Atlas Search indexes on the chunks collection, and keeping them in place.

The vector index covers `embedding` at 768 dimensions with `dotProduct` similarity, which equals
cosine similarity for unit-length vectors. The keyword index covers the chunk's text, name,
qualified name, and file path for BM25 scoring. Both declare `repository`, `version`, and
`is_test_file` so searches can filter while they search.

The keyword index analyzes code with a custom `code` analyzer:
1. The tokenizer splits at every character that cannot be part of a Python identifier, so
   `app.add_url_rule("/",` yields `app` and `add_url_rule`.
2. `wordDelimiterGraph` splits each identifier at underscores, case changes, and digits, and keeps
   the original: `add_url_rule` is indexed as `add_url_rule`, `add`, `url`, and `rule`, and
   `AppContext` as `AppContext`, `App`, and `Context`. `flattenGraph` makes that result storable.
3. Lowercasing comes last, so the case changes are still there when identifiers are split.
There is no stemming and no stop-word list: keyword search is for exact names, vector search
handles meaning, and BM25 already gives words found everywhere almost no weight.

The keyword mapping is not dynamic, so only the listed fields are indexed; the embedding never is.

`ensure_search_index` creates a missing index, updates one whose definition differs, and otherwise
leaves it alone. An update rebuilds the index, so "differs" means the stored definition lacks
something set here; defaults Atlas adds to the stored copy are ignored. A free M0 cluster allows
three search indexes, and these two use two of them.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pymongo.collection import Collection
from pymongo.operations import SearchIndexModel

from retrieval.config import CODE_ANALYZER_NAME, KEYWORD_INDEX_NAME, VECTOR_INDEX_NAME
from retrieval.search_results import SearchRefusedError

VECTOR_SEARCH_INDEX_TYPE = "vectorSearch"

KEYWORD_SEARCH_INDEX_TYPE = "search"

EMBEDDING_FIELD = "embedding"

SIMILARITY = "dotProduct"

FILTER_FIELDS = ("repository", "version", "is_test_file")

KEYWORD_TEXT_FIELDS = ("text", "name", "qualified_name", "file_path")

NON_IDENTIFIER_CHARACTERS_PATTERN = r"[^\p{L}\p{N}_]+"


class SearchIndexChange(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def vector_index_definition(dimensions: int) -> dict[str, Any]:
    vector_field = {
        "type": "vector",
        "path": EMBEDDING_FIELD,
        "numDimensions": dimensions,
        "similarity": SIMILARITY,
    }
    filter_fields = [{"type": "filter", "path": field_path} for field_path in FILTER_FIELDS]
    return {"fields": [vector_field, *filter_fields]}


def keyword_index_definition() -> dict[str, Any]:
    code_analyzer = {
        "name": CODE_ANALYZER_NAME,
        "tokenizer": {"type": "regexSplit", "pattern": NON_IDENTIFIER_CHARACTERS_PATTERN},
        "tokenFilters": [
            {
                "type": "wordDelimiterGraph",
                "delimiterOptions": {
                    "generateWordParts": True,
                    "generateNumberParts": True,
                    "splitOnCaseChange": True,
                    "splitOnNumerics": True,
                    "preserveOriginal": True,
                },
            },
            {"type": "flattenGraph"},
            {"type": "lowercase"},
        ],
    }
    text_fields = {
        field_name: {"type": "string", "analyzer": CODE_ANALYZER_NAME}
        for field_name in KEYWORD_TEXT_FIELDS
    }
    filter_fields = {
        "repository": {"type": "token"},
        "version": {"type": "token"},
        "is_test_file": {"type": "boolean"},
    }
    return {
        "analyzers": [code_analyzer],
        "mappings": {"dynamic": False, "fields": {**text_fields, **filter_fields}},
    }


def ensure_vector_index(chunks_collection: Collection, dimensions: int) -> SearchIndexChange:
    return ensure_search_index(
        chunks_collection,
        VECTOR_INDEX_NAME,
        VECTOR_SEARCH_INDEX_TYPE,
        vector_index_definition(dimensions),
    )


def ensure_keyword_index(chunks_collection: Collection) -> SearchIndexChange:
    return ensure_search_index(
        chunks_collection, KEYWORD_INDEX_NAME, KEYWORD_SEARCH_INDEX_TYPE, keyword_index_definition()
    )


def ensure_search_index(
    chunks_collection: Collection, name: str, index_type: str, definition: dict[str, Any]
) -> SearchIndexChange:
    existing_index = _find_search_index(chunks_collection, name)
    if existing_index is None:
        index_model = SearchIndexModel(definition=definition, name=name, type=index_type)
        chunks_collection.create_search_index(index_model)
        return SearchIndexChange.CREATED
    if definition_contains(existing_index.get("latestDefinition", {}), definition):
        return SearchIndexChange.UNCHANGED
    chunks_collection.update_search_index(name, definition)
    return SearchIndexChange.UPDATED


def require_queryable_index(chunks_collection: Collection, name: str) -> None:
    existing_index = _find_search_index(chunks_collection, name)
    if existing_index is None:
        raise SearchRefusedError(
            f"The search index {name} does not exist; run index_repository.py first"
        )
    if not existing_index.get("queryable", False):
        status = existing_index.get("status", "unknown")
        raise SearchRefusedError(
            f"The search index {name} is still building (status {status}); try again in a minute"
        )


def definition_contains(stored: Any, wanted: Any) -> bool:
    """Whether everything in `wanted` is in `stored`, ignoring anything extra Atlas added.

    Dictionaries must hold every wanted key; lists must be the same length, with every wanted
    item contained in some stored item, in any order; other values must be equal.
    """
    if isinstance(wanted, dict):
        if not isinstance(stored, dict):
            return False
        return all(
            key in stored and definition_contains(stored[key], wanted_value)
            for key, wanted_value in wanted.items()
        )
    if isinstance(wanted, list):
        if not isinstance(stored, list) or len(stored) != len(wanted):
            return False
        return all(
            any(definition_contains(stored_item, wanted_item) for stored_item in stored)
            for wanted_item in wanted
        )
    return stored == wanted


def _find_search_index(chunks_collection: Collection, name: str) -> dict[str, Any] | None:
    matching_indexes = list(chunks_collection.list_search_indexes(name))
    if not matching_indexes:
        return None
    return matching_indexes[0]
