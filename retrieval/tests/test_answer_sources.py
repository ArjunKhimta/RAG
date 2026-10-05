from __future__ import annotations

from typing import Any

import pytest

from retrieval.answer_sources import expand_sources, find_sources
from retrieval.query_router import QueryRoute, route_query, vector_without_router
from retrieval.reranking import PairScores
from retrieval.search_results import SearchResult

APP_FILE = "src/pkg/app.py"


def _document(qualified_name: str, start_line: int) -> dict[str, Any]:
    return {
        "_id": f"owner/repo@1.0:{APP_FILE}:{start_line}:function:1",
        "file_path": APP_FILE,
        "start_line": start_line,
        "end_line": start_line + 1,
        "kind": "function",
        "qualified_name": qualified_name,
        "symbol": f"{APP_FILE}::{qualified_name}",
        "signature": None,
        "part_number": 1,
        "part_count": 1,
        "is_test_file": False,
        "text": f"def {qualified_name}():\n    pass",
    }


def _source(qualified_name: str, start_line: int) -> SearchResult:
    document = _document(qualified_name, start_line)
    return SearchResult(
        chunk_id=document["_id"],
        file_path=APP_FILE,
        start_line=start_line,
        end_line=start_line + 1,
        kind="function",
        qualified_name=qualified_name,
        signature=None,
        part_number=1,
        part_count=1,
        is_test_file=False,
        text=document["text"],
        score=1.0,
    )


class FakeCollection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.pipelines: list[list[dict[str, Any]]] = []

    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.pipelines.append(pipeline)
        return self.documents


class NameScorer:
    """Scores a passage by the function name it contains, so tests choose the ranking."""

    def __init__(self, scores_by_name: dict[str, float]) -> None:
        self.scores_by_name = scores_by_name
        self.scored_passage_counts: list[int] = []

    def score_pairs(self, question: str, passages: list[str]) -> PairScores:
        self.scored_passage_counts.append(len(passages))
        scores = [self._score_of(passage) for passage in passages]
        return PairScores(scores=scores, truncated=[False] * len(passages))

    def _score_of(self, passage: str) -> float:
        for name, score in self.scores_by_name.items():
            if f"def {name}()" in passage:
                return score
        return 0.0


def test_the_best_neighbors_follow_the_sources_with_notes_naming_their_source():
    sources = [_source("run", 10), _source("serve", 40)]
    collection = FakeCollection(
        [
            {
                "_id": sources[0].chunk_id,
                "callees": [_document("prepare", 20), _document("validate", 25)],
                "callers": [],
            },
            {"_id": sources[1].chunk_id, "callees": [], "callers": [_document("main", 1)]},
        ]
    )
    scorer = NameScorer({"prepare": 0.1, "validate": 0.9, "main": 0.5})

    expanded = expand_sources(
        collection, scorer, "question", sources, "owner/repo", "1.0", kept_count=2
    )

    assert [source.qualified_name for source in expanded.sources] == [
        "run",
        "serve",
        "validate",
        "main",
    ]
    assert expanded.related_notes == [None, None, "called by source 1", "calls source 2"]
    assert expanded.found_neighbor_count == 3
    assert [kept.rerank_score for kept in expanded.kept_neighbors] == [0.9, 0.5]
    assert set(expanded.timings) == {"expand context", "rerank neighbors"}


def test_only_the_first_candidates_in_source_order_are_reranked():
    sources = [_source("run", 10)]
    callees = [_document(f"step_{index}", 100 + index * 10) for index in range(5)]
    collection = FakeCollection([{"_id": sources[0].chunk_id, "callees": callees, "callers": []}])
    scorer = NameScorer({"step_4": 9.0})

    expanded = expand_sources(
        collection, scorer, "question", sources, "owner/repo", "1.0", candidate_limit=3
    )

    assert scorer.scored_passage_counts == [3]
    assert expanded.found_neighbor_count == 5
    assert "step_4" not in [kept.neighbor.result.qualified_name for kept in expanded.kept_neighbors]


def test_with_no_neighbors_the_sources_come_back_unchanged_and_nothing_is_reranked():
    sources = [_source("run", 10)]
    collection = FakeCollection([{"_id": sources[0].chunk_id, "callees": [], "callers": []}])
    scorer = NameScorer({})

    expanded = expand_sources(collection, scorer, "question", sources, "owner/repo", "1.0")

    assert expanded.sources == sources
    assert expanded.related_notes == [None]
    assert expanded.kept_neighbors == []
    assert scorer.scored_passage_counts == []
    assert set(expanded.timings) == {"expand context"}


class RefusingEmbedder:
    """Fails the test if the question is ever embedded."""

    model_id = "fake-model"
    dimensions = 3
    task_type = "CODE_RETRIEVAL_QUERY"

    def embed_query(self, question: str) -> list[float]:
        raise AssertionError("a code name must not be embedded")


def test_a_code_name_takes_keyword_search_top_5_without_embedding_or_reranking():
    keyword_documents = [
        {**_document(f"url_for_{index}", index * 10), "score": 9.0 - index} for index in range(5)
    ]
    collection = FakeCollection(keyword_documents)

    found = find_sources(
        collection,
        RefusingEmbedder(),
        None,
        "`url_for()`",
        "owner/repo",
        "1.0",
        route_query("`url_for()`"),
    )

    assert [source.qualified_name for source in found.sources] == [
        f"url_for_{index}" for index in range(5)
    ]
    assert found.route.route == QueryRoute.KEYWORD
    assert set(found.timings) == {"keyword search"}
    search_stage = collection.pipelines[0][0]["$search"]
    assert "url_for" in str(search_stage)
    assert "`" not in str(search_stage)


class RecordingEmbedder:
    """Returns a fixed vector and records each question it embeds."""

    model_id = "fake-model"
    dimensions = 3
    task_type = "CODE_RETRIEVAL_QUERY"

    def __init__(self) -> None:
        self.embedded_questions: list[str] = []

    def embed_query(self, question: str) -> list[float]:
        self.embedded_questions.append(question)
        return [0.6, 0.0, 0.8]


def test_the_vector_route_takes_vector_search_top_5_without_reranking():
    vector_documents = [
        {**_document(f"build_url_{index}", index * 10), "score": 0.9 - index / 10}
        for index in range(5)
    ]
    collection = FakeCollection(vector_documents)
    embedder = RecordingEmbedder()
    question = "How are URLs built?"

    found = find_sources(
        collection,
        embedder,
        None,
        question,
        "owner/repo",
        "1.0",
        vector_without_router(question),
        exclude_tests=True,
    )

    assert [source.qualified_name for source in found.sources] == [
        f"build_url_{index}" for index in range(5)
    ]
    assert found.route.route == QueryRoute.VECTOR
    assert embedder.embedded_questions == [question]
    assert set(found.timings) == {"embed query", "vector search"}
    search_stage = collection.pipelines[0][0]["$vectorSearch"]
    assert search_stage["queryVector"] == [0.6, 0.0, 0.8]
    assert search_stage["limit"] == 5
    assert {"is_test_file": {"$eq": False}} in search_stage["filter"]["$and"]


def test_the_hybrid_route_needs_the_reranker():
    with pytest.raises(ValueError, match="scorer"):
        find_sources(
            FakeCollection([]),
            RefusingEmbedder(),
            None,
            "How are URLs built?",
            "owner/repo",
            "1.0",
            route_query("How are URLs built?"),
        )
