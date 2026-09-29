from __future__ import annotations

import copy

import pytest

from retrieval.config import CODE_ANALYZER_NAME, KEYWORD_INDEX_NAME, VECTOR_INDEX_NAME
from retrieval.search_indexes import (
    SearchIndexChange,
    definition_contains,
    ensure_keyword_index,
    ensure_vector_index,
    keyword_index_definition,
    require_queryable_index,
    vector_index_definition,
)
from retrieval.search_results import SearchRefusedError


class FakeChunksCollection:
    def __init__(self, search_indexes=None):
        self.search_indexes = search_indexes or []
        self.created_models = []
        self.updated_definitions = []

    def list_search_indexes(self, name):
        return [index for index in self.search_indexes if index["name"] == name]

    def create_search_index(self, model):
        self.created_models.append(model)

    def update_search_index(self, name, definition):
        self.updated_definitions.append((name, definition))


def _stored_index(name, definition, **fields):
    return {"name": name, "latestDefinition": definition, **fields}


def test_the_vector_index_covers_the_embedding_with_dot_product_and_declares_filter_fields():
    definition = vector_index_definition(768)

    vector_field, *filter_fields = definition["fields"]
    assert vector_field == {
        "type": "vector",
        "path": "embedding",
        "numDimensions": 768,
        "similarity": "dotProduct",
    }
    assert [field["path"] for field in filter_fields] == ["repository", "version", "is_test_file"]
    assert all(field["type"] == "filter" for field in filter_fields)


def test_the_keyword_index_maps_only_the_listed_fields_and_never_the_embedding():
    mappings = keyword_index_definition()["mappings"]

    assert mappings["dynamic"] is False
    assert "embedding" not in mappings["fields"]
    assert mappings["fields"]["repository"] == {"type": "token"}
    assert mappings["fields"]["version"] == {"type": "token"}
    assert mappings["fields"]["is_test_file"] == {"type": "boolean"}


def test_the_keyword_index_analyzes_text_names_and_paths_as_code():
    fields = keyword_index_definition()["mappings"]["fields"]

    for field_name in ("text", "name", "qualified_name", "file_path"):
        assert fields[field_name] == {"type": "string", "analyzer": CODE_ANALYZER_NAME}


def test_the_code_analyzer_splits_identifiers_keeps_originals_and_lowercases_last():
    (analyzer,) = keyword_index_definition()["analyzers"]

    filter_types = [token_filter["type"] for token_filter in analyzer["tokenFilters"]]
    delimiter_options = analyzer["tokenFilters"][0]["delimiterOptions"]
    assert analyzer["name"] == CODE_ANALYZER_NAME
    assert analyzer["tokenizer"]["type"] == "regexSplit"
    assert filter_types == ["wordDelimiterGraph", "flattenGraph", "lowercase"]
    assert delimiter_options["preserveOriginal"] is True
    assert delimiter_options["splitOnCaseChange"] is True


def test_a_missing_vector_index_is_created_as_a_vector_search_index():
    collection = FakeChunksCollection()

    change = ensure_vector_index(collection, 768)

    created_document = collection.created_models[0].document
    assert change == SearchIndexChange.CREATED
    assert created_document["name"] == VECTOR_INDEX_NAME
    assert created_document["type"] == "vectorSearch"
    assert created_document["definition"] == vector_index_definition(768)


def test_a_missing_keyword_index_is_created_as_a_search_index():
    collection = FakeChunksCollection()

    change = ensure_keyword_index(collection)

    created_document = collection.created_models[0].document
    assert change == SearchIndexChange.CREATED
    assert created_document["name"] == KEYWORD_INDEX_NAME
    assert created_document["type"] == "search"
    assert created_document["definition"] == keyword_index_definition()


def test_an_index_whose_stored_copy_has_extra_atlas_defaults_is_left_alone():
    stored_definition = keyword_index_definition()
    stored_definition["mappings"]["fields"]["text"]["indexOptions"] = "offsets"
    stored_definition["analyzers"][0]["charFilters"] = []
    stored_definition["storedSource"] = False
    collection = FakeChunksCollection(
        search_indexes=[_stored_index(KEYWORD_INDEX_NAME, stored_definition)]
    )

    change = ensure_keyword_index(collection)

    assert change == SearchIndexChange.UNCHANGED
    assert collection.created_models == []
    assert collection.updated_definitions == []


def test_a_vector_index_with_different_dimensions_is_updated():
    collection = FakeChunksCollection(
        search_indexes=[_stored_index(VECTOR_INDEX_NAME, vector_index_definition(3072))]
    )

    change = ensure_vector_index(collection, 768)

    assert change == SearchIndexChange.UPDATED
    assert collection.updated_definitions == [(VECTOR_INDEX_NAME, vector_index_definition(768))]


def test_a_keyword_index_with_a_changed_analyzer_is_updated():
    stored_definition = keyword_index_definition()
    stored_definition["analyzers"][0]["tokenFilters"].pop()
    collection = FakeChunksCollection(
        search_indexes=[_stored_index(KEYWORD_INDEX_NAME, stored_definition)]
    )

    change = ensure_keyword_index(collection)

    assert change == SearchIndexChange.UPDATED


def test_containment_ignores_the_order_of_list_items():
    wanted = vector_index_definition(768)
    stored = copy.deepcopy(wanted)
    stored["fields"].reverse()

    assert definition_contains(stored, wanted)


@pytest.mark.parametrize(
    ("stored", "wanted"),
    [
        ({"a": 1}, {"a": 2}),
        ({}, {"a": 1}),
        ([{"a": 1}], [{"a": 1}, {"b": 2}]),
        ([{"a": 1}, {"b": 2}], [{"a": 1}]),
        ("text", {"a": 1}),
    ],
)
def test_containment_detects_missing_or_different_values(stored, wanted):
    assert not definition_contains(stored, wanted)


def test_searching_is_refused_without_the_index():
    with pytest.raises(SearchRefusedError, match="does not exist"):
        require_queryable_index(FakeChunksCollection(), KEYWORD_INDEX_NAME)


def test_searching_is_refused_while_the_index_is_building():
    collection = FakeChunksCollection(
        search_indexes=[
            _stored_index(KEYWORD_INDEX_NAME, {}, queryable=False, status="PENDING")
        ]
    )

    with pytest.raises(SearchRefusedError, match="still building"):
        require_queryable_index(collection, KEYWORD_INDEX_NAME)


def test_a_queryable_index_is_accepted():
    collection = FakeChunksCollection(
        search_indexes=[_stored_index(VECTOR_INDEX_NAME, {}, queryable=True, status="READY")]
    )

    require_queryable_index(collection, VECTOR_INDEX_NAME)
