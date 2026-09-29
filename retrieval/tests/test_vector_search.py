from __future__ import annotations

from types import SimpleNamespace

import pytest

from retrieval.config import MAX_SEARCH_LIMIT, VECTOR_INDEX_NAME
from retrieval.search_results import SearchRefusedError
from retrieval.vector_search import (
    SearchOptions,
    build_vector_search_pipeline,
    require_searchable_version,
    search_chunks,
)

QUERY_VECTOR = [0.6, 0.8]

QUERY_EMBEDDER = SimpleNamespace(
    model_id="gemini-embedding-001", dimensions=768, task_type="CODE_RETRIEVAL_QUERY"
)

REPOSITORY_RECORD = {
    "_id": "pallets/flask@3.1.3",
    "embedding_model": "gemini-embedding-001",
    "embedding_dimensions": 768,
}

RESULT_DOCUMENT = {
    "_id": "pallets/flask@3.1.3:src/flask/app.py:546:method:1",
    "file_path": "src/flask/app.py",
    "start_line": 546,
    "end_line": 636,
    "kind": "method",
    "qualified_name": "Flask.run",
    "signature": None,
    "part_number": 1,
    "part_count": 1,
    "is_test_file": False,
    "text": "    def run(self):\n        ...",
    "score": 0.87,
}


class FakeChunksCollection:
    def __init__(self, documents=None):
        self.documents = documents or []
        self.pipelines = []

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return iter(self.documents)


def _search_stage(options: SearchOptions) -> dict:
    pipeline = build_vector_search_pipeline(QUERY_VECTOR, "pallets/flask", "3.1.3", options)
    return pipeline[0]["$vectorSearch"]


def test_searching_is_refused_until_the_version_has_finished_indexing():
    with pytest.raises(SearchRefusedError, match="has not finished indexing"):
        require_searchable_version(None, "pallets/flask", "3.1.3", QUERY_EMBEDDER)


def test_searching_is_refused_when_the_version_was_embedded_with_another_model():
    record = {**REPOSITORY_RECORD, "embedding_model": "another-model"}

    with pytest.raises(SearchRefusedError, match="cannot be compared"):
        require_searchable_version(record, "pallets/flask", "3.1.3", QUERY_EMBEDDER)


def test_searching_is_refused_when_the_dimensions_differ():
    record = {**REPOSITORY_RECORD, "embedding_dimensions": 3072}

    with pytest.raises(SearchRefusedError, match="cannot be compared"):
        require_searchable_version(record, "pallets/flask", "3.1.3", QUERY_EMBEDDER)


def test_a_fully_indexed_version_with_the_same_model_is_searchable():
    require_searchable_version(REPOSITORY_RECORD, "pallets/flask", "3.1.3", QUERY_EMBEDDER)


def test_the_search_is_filtered_to_one_repository_version_including_tests_by_default():
    search_stage = _search_stage(SearchOptions())

    assert search_stage["index"] == VECTOR_INDEX_NAME
    assert search_stage["path"] == "embedding"
    assert search_stage["queryVector"] == QUERY_VECTOR
    assert search_stage["filter"] == {
        "$and": [{"repository": {"$eq": "pallets/flask"}}, {"version": {"$eq": "3.1.3"}}]
    }


def test_test_files_can_be_excluded():
    search_stage = _search_stage(SearchOptions(exclude_tests=True))

    assert {"is_test_file": {"$eq": False}} in search_stage["filter"]["$and"]


def test_an_approximate_search_explores_twenty_candidates_per_result():
    search_stage = _search_stage(SearchOptions(limit=5))

    assert search_stage["limit"] == 5
    assert search_stage["numCandidates"] == 100
    assert "exact" not in search_stage


def test_an_exact_search_sets_exact_and_leaves_out_the_candidate_count():
    search_stage = _search_stage(SearchOptions(limit=5, exact=True))

    assert search_stage["exact"] is True
    assert "numCandidates" not in search_stage


def test_the_score_is_projected_from_the_vector_search_metadata():
    pipeline = build_vector_search_pipeline(QUERY_VECTOR, "pallets/flask", "3.1.3", SearchOptions())

    projected_fields = pipeline[1]["$project"]
    assert projected_fields["score"] == {"$meta": "vectorSearchScore"}
    assert "embedding" not in projected_fields


@pytest.mark.parametrize("limit", [0, MAX_SEARCH_LIMIT + 1])
def test_a_limit_outside_the_allowed_range_is_rejected(limit):
    with pytest.raises(ValueError):
        SearchOptions(limit=limit)


def test_search_results_keep_the_order_location_and_score_atlas_returns():
    second_document = {**RESULT_DOCUMENT, "_id": "second", "score": 0.52, "is_test_file": True}
    collection = FakeChunksCollection(documents=[RESULT_DOCUMENT, second_document])

    results = search_chunks(collection, QUERY_VECTOR, "pallets/flask", "3.1.3", SearchOptions())

    assert [result.chunk_id for result in results] == [RESULT_DOCUMENT["_id"], "second"]
    assert results[0].file_path == "src/flask/app.py"
    assert (results[0].start_line, results[0].end_line) == (546, 636)
    assert results[0].qualified_name == "Flask.run"
    assert results[0].score == pytest.approx(0.87)
    assert results[1].is_test_file is True
    assert len(collection.pipelines) == 1
