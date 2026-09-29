from __future__ import annotations

import pytest

from retrieval.config import KEYWORD_INDEX_NAME, MAX_SEARCH_LIMIT, NAME_FIELD_BOOST
from retrieval.keyword_search import (
    KeywordSearchOptions,
    build_keyword_search_pipeline,
    search_chunks_by_keywords,
)

RESULT_DOCUMENT = {
    "_id": "pallets/flask@3.1.3:src/flask/sansio/scaffold.py:361:method:1",
    "file_path": "src/flask/sansio/scaffold.py",
    "start_line": 361,
    "end_line": 430,
    "kind": "method",
    "qualified_name": "Scaffold.add_url_rule",
    "signature": None,
    "part_number": 1,
    "part_count": 1,
    "is_test_file": False,
    "text": "    def add_url_rule(self, rule, endpoint=None, view_func=None):\n        ...",
    "score": 14.2,
}


class FakeChunksCollection:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return iter(self.documents)


def _compound(options: KeywordSearchOptions) -> dict:
    pipeline = build_keyword_search_pipeline("add_url_rule", "pallets/flask", "3.1.3", options)
    return pipeline[0]["$search"]["compound"]


def test_the_search_uses_the_keyword_index():
    pipeline = build_keyword_search_pipeline(
        "add_url_rule", "pallets/flask", "3.1.3", KeywordSearchOptions()
    )

    assert pipeline[0]["$search"]["index"] == KEYWORD_INDEX_NAME


def test_name_matches_are_boosted_over_text_and_path_matches():
    name_clause, body_clause = _compound(KeywordSearchOptions())["should"]

    assert name_clause["text"]["query"] == "add_url_rule"
    assert name_clause["text"]["path"] == ["name", "qualified_name"]
    assert name_clause["text"]["score"] == {"boost": {"value": NAME_FIELD_BOOST}}
    assert body_clause["text"]["query"] == "add_url_rule"
    assert body_clause["text"]["path"] == ["text", "file_path"]
    assert "score" not in body_clause["text"]


def test_at_least_one_clause_must_match():
    assert _compound(KeywordSearchOptions())["minimumShouldMatch"] == 1


def test_the_search_is_filtered_to_one_repository_version_including_tests_by_default():
    filter_clauses = _compound(KeywordSearchOptions())["filter"]

    assert filter_clauses == [
        {"equals": {"path": "repository", "value": "pallets/flask"}},
        {"equals": {"path": "version", "value": "3.1.3"}},
    ]


def test_test_files_can_be_excluded():
    filter_clauses = _compound(KeywordSearchOptions(exclude_tests=True))["filter"]

    assert {"equals": {"path": "is_test_file", "value": False}} in filter_clauses


def test_the_limit_follows_the_search_and_the_score_is_the_bm25_score():
    pipeline = build_keyword_search_pipeline(
        "add_url_rule", "pallets/flask", "3.1.3", KeywordSearchOptions(limit=5)
    )

    assert pipeline[1] == {"$limit": 5}
    assert pipeline[2]["$project"]["score"] == {"$meta": "searchScore"}
    assert "embedding" not in pipeline[2]["$project"]


@pytest.mark.parametrize("limit", [0, MAX_SEARCH_LIMIT + 1])
def test_a_limit_outside_the_allowed_range_is_rejected(limit):
    with pytest.raises(ValueError):
        KeywordSearchOptions(limit=limit)


def test_search_results_keep_the_order_and_bm25_score_atlas_returns():
    second_document = {**RESULT_DOCUMENT, "_id": "second", "score": 3.1}
    collection = FakeChunksCollection(documents=[RESULT_DOCUMENT, second_document])

    results = search_chunks_by_keywords(
        collection, "add_url_rule", "pallets/flask", "3.1.3", KeywordSearchOptions()
    )

    assert [result.chunk_id for result in results] == [RESULT_DOCUMENT["_id"], "second"]
    assert results[0].qualified_name == "Scaffold.add_url_rule"
    assert results[0].score == pytest.approx(14.2)
    assert len(collection.pipelines) == 1
