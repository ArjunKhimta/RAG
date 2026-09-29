from __future__ import annotations

import pytest

from retrieval.config import HYBRID_CANDIDATE_DEPTH, MAX_SEARCH_LIMIT
from retrieval.hybrid_search import HybridSearchOptions, hybrid_search

QUERY_VECTOR = [0.6, 0.8]


class FakeQueryEmbedder:
    model_id = "gemini-embedding-001"
    dimensions = 2
    task_type = "CODE_RETRIEVAL_QUERY"

    def __init__(self):
        self.questions = []

    def embed_query(self, question):
        self.questions.append(question)
        return QUERY_VECTOR


class FakeChunksCollection:
    """Answers `$vectorSearch` and `$search` pipelines with separate document lists."""

    def __init__(self, vector_documents, keyword_documents):
        self.vector_documents = vector_documents
        self.keyword_documents = keyword_documents
        self.pipelines = {}

    def aggregate(self, pipeline):
        first_stage_name = next(iter(pipeline[0]))
        self.pipelines[first_stage_name] = pipeline
        if first_stage_name == "$vectorSearch":
            return iter(self.vector_documents)
        return iter(self.keyword_documents)


def _document(chunk_id: str, score: float, is_test_file: bool = False) -> dict:
    return {
        "_id": chunk_id,
        "file_path": "pkg/module.py",
        "start_line": 1,
        "end_line": 2,
        "kind": "function",
        "qualified_name": chunk_id,
        "signature": None,
        "part_number": 1,
        "part_count": 1,
        "is_test_file": is_test_file,
        "text": f"def {chunk_id}():\n    pass",
        "score": score,
    }


def _collection() -> FakeChunksCollection:
    return FakeChunksCollection(
        vector_documents=[_document("semantic_match", 0.9), _document("shared", 0.8)],
        keyword_documents=[_document("shared", 12.0), _document("name_match", 7.5)],
    )


def _search(collection, options=None, embedder=None):
    return hybrid_search(
        collection,
        embedder or FakeQueryEmbedder(),
        "How are routes registered?",
        "pallets/flask",
        "3.1.3",
        options or HybridSearchOptions(),
    )


def test_the_question_is_embedded_for_vector_search_and_sent_as_text_to_keyword_search():
    collection = _collection()
    embedder = FakeQueryEmbedder()

    _search(collection, embedder=embedder)

    vector_stage = collection.pipelines["$vectorSearch"][0]["$vectorSearch"]
    keyword_stage = collection.pipelines["$search"][0]["$search"]
    assert embedder.questions == ["How are routes registered?"]
    assert vector_stage["queryVector"] == QUERY_VECTOR
    assert keyword_stage["compound"]["should"][0]["text"]["query"] == "How are routes registered?"


def test_both_searches_fetch_the_candidate_depth():
    collection = _collection()

    _search(collection, HybridSearchOptions(limit=5))

    vector_stage = collection.pipelines["$vectorSearch"][0]["$vectorSearch"]
    keyword_limit_stage = collection.pipelines["$search"][1]
    assert vector_stage["limit"] == HYBRID_CANDIDATE_DEPTH
    assert keyword_limit_stage == {"$limit": HYBRID_CANDIDATE_DEPTH}


def test_a_limit_above_the_candidate_depth_raises_the_depth():
    options = HybridSearchOptions(limit=HYBRID_CANDIDATE_DEPTH + 10)

    assert options.candidate_depth == HYBRID_CANDIDATE_DEPTH + 10


def test_excluding_tests_reaches_both_searches():
    collection = _collection()

    _search(collection, HybridSearchOptions(exclude_tests=True))

    vector_filter = collection.pipelines["$vectorSearch"][0]["$vectorSearch"]["filter"]["$and"]
    keyword_filter = collection.pipelines["$search"][0]["$search"]["compound"]["filter"]
    assert {"is_test_file": {"$eq": False}} in vector_filter
    assert {"equals": {"path": "is_test_file", "value": False}} in keyword_filter


def test_exact_applies_to_the_vector_search():
    collection = _collection()

    _search(collection, HybridSearchOptions(exact=True))

    assert collection.pipelines["$vectorSearch"][0]["$vectorSearch"]["exact"] is True


def test_the_results_are_fused_with_ranks_from_each_list():
    outcome = _search(_collection())

    fused_ids = [fused.result.chunk_id for fused in outcome.results]
    shared = outcome.results[0]
    assert fused_ids[0] == "shared"
    assert set(fused_ids) == {"shared", "semantic_match", "name_match"}
    assert shared.ranks == {"vector": 2, "keyword": 1}
    assert shared.fused_score == pytest.approx(1 / 62 + 1 / 61)


def test_the_fused_list_is_cut_to_the_limit():
    outcome = _search(_collection(), HybridSearchOptions(limit=2))

    assert len(outcome.results) == 2


def test_every_stage_is_timed():
    outcome = _search(_collection())

    assert list(outcome.timings) == [
        "embed query",
        "vector search",
        "keyword search",
        "rank fusion",
    ]
    assert all(milliseconds >= 0 for milliseconds in outcome.timings.values())


@pytest.mark.parametrize("limit", [0, MAX_SEARCH_LIMIT + 1])
def test_a_limit_outside_the_allowed_range_is_rejected(limit):
    with pytest.raises(ValueError):
        HybridSearchOptions(limit=limit)
