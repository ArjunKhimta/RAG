from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from retrieval import asking
from retrieval.answer_sources import FoundSources
from retrieval.asking import (
    EmbeddingUse,
    NoSourcesFoundError,
    QuestionAsker,
    route_for,
)
from retrieval.chunk_store import CHUNKS_COLLECTION
from retrieval.config import KEYWORD_INDEX_NAME, VECTOR_INDEX_NAME
from retrieval.query_router import QueryRoute
from retrieval.search_results import SearchRefusedError, SearchResult

REPOSITORY_RECORD = {
    "commit_id": "c" * 40,
    "license_spdx_id": "BSD-3-Clause",
    "license_path": "LICENSE.txt",
}

SOURCE = SearchResult(
    chunk_id="source",
    file_path="src/flask/app.py",
    start_line=10,
    end_line=12,
    kind="function",
    qualified_name="run",
    signature=None,
    part_number=1,
    part_count=1,
    is_test_file=False,
    text="def run():\n    pass\n",
    score=0.9,
)


@dataclass
class FakeEmbedder:
    hit_count: int = 0
    miss_count: int = 0
    requests: list[FakeEmbedder] = field(default_factory=list)

    def for_one_request(self) -> FakeEmbedder:
        request_embedder = FakeEmbedder()
        self.requests.append(request_embedder)
        return request_embedder


@dataclass
class FakeStore:
    database: object

    def find_repository_record(self, repository, version):
        return REPOSITORY_RECORD


@dataclass
class Calls:
    find_sources: list[dict] = field(default_factory=list)
    generate_answer: list[dict] = field(default_factory=list)
    call_graph_checks: int = 0
    scorer_loads: int = 0
    checked_indexes: list[str] = field(default_factory=list)
    rechecked_indexes: list[str] = field(default_factory=list)


@dataclass
class FakeIndexReadiness:
    calls: Calls

    def require_queryable(self, name):
        self.calls.checked_indexes.append(name)

    def recheck(self, name):
        self.calls.rechecked_indexes.append(name)


@pytest.fixture
def calls(monkeypatch) -> Calls:
    recorded = Calls()
    monkeypatch.setattr(asking, "MongoChunkStore", FakeStore)
    monkeypatch.setattr(asking, "require_indexed_version", lambda *arguments: None)
    monkeypatch.setattr(asking, "require_searchable_version", lambda *arguments: None)

    def require_call_graph(*arguments):
        recorded.call_graph_checks += 1

    monkeypatch.setattr(asking, "require_call_graph", require_call_graph)

    def generate_answer(question, sources, model, related_notes):
        recorded.generate_answer.append({"model": model, "sources": sources})
        return "generated answer"

    monkeypatch.setattr(asking, "generate_answer", generate_answer)
    return recorded


def _find_sources_returning(calls: Calls, sources: list[SearchResult], embedding: str = "miss"):
    def find_sources(
        chunks_collection,
        embedder,
        scorer,
        question,
        repository,
        version,
        route,
        exclude_tests=False,
    ):
        calls.find_sources.append(
            {"collection": chunks_collection, "scorer": scorer, "exclude_tests": exclude_tests}
        )
        if embedding == "miss":
            embedder.miss_count += 1
        elif embedding == "hit":
            embedder.hit_count += 1
        return FoundSources(sources=sources, timings={"search": 5.0}, route=route)

    return find_sources


def _asker(calls: Calls, embedder: FakeEmbedder | None = None) -> QuestionAsker:
    def load_scorer():
        calls.scorer_loads += 1
        return "scorer"

    return QuestionAsker(
        {CHUNKS_COLLECTION: "chunks collection"},
        embedder or FakeEmbedder(),
        "answer model",
        load_scorer,
        FakeIndexReadiness(calls),
    )


@pytest.mark.parametrize(
    "search, question, expected_route",
    [
        ("vector", "How is the cookie signed?", QueryRoute.VECTOR),
        ("vector", "url_for", QueryRoute.VECTOR),
        ("router", "url_for", QueryRoute.KEYWORD),
        ("router", "How is the cookie signed?", QueryRoute.HYBRID),
        ("hybrid", "url_for", QueryRoute.HYBRID),
    ],
)
def test_each_search_choice_picks_its_route(search, question, expected_route):
    assert route_for(search, question).route == expected_route


def test_an_unknown_search_is_refused():
    with pytest.raises(ValueError, match="Unknown search"):
        route_for("semantic", "question")


def test_vector_search_never_loads_the_reranker(monkeypatch, calls):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE]))

    run = _asker(calls).ask("How is the cookie signed?", "pallets/flask", "3.1.3")

    assert calls.scorer_loads == 0
    assert calls.find_sources[0]["scorer"] is None
    assert "load reranker" not in run.timings
    assert run.generated == "generated answer"
    assert run.repository_record == REPOSITORY_RECORD


def test_the_reranker_loads_once_and_only_the_first_load_is_timed(monkeypatch, calls):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE]))
    asker = _asker(calls)

    first = asker.ask("How is the cookie signed?", "pallets/flask", "3.1.3", search="hybrid")
    second = asker.ask("Where are routes added?", "pallets/flask", "3.1.3", search="hybrid")

    assert calls.scorer_loads == 1
    assert [call["scorer"] for call in calls.find_sources] == ["scorer", "scorer"]
    assert "load reranker" in first.timings
    assert "load reranker" not in second.timings


def test_every_question_uses_the_same_answer_model(monkeypatch, calls):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE]))
    asker = _asker(calls)

    asker.ask("first question", "pallets/flask", "3.1.3")
    asker.ask("second question", "pallets/flask", "3.1.3")

    assert [call["model"] for call in calls.generate_answer] == ["answer model", "answer model"]


@pytest.mark.parametrize(
    "embedding, expected_use",
    [
        ("miss", EmbeddingUse.EMBEDDED),
        ("hit", EmbeddingUse.FROM_CACHE),
        ("none", EmbeddingUse.NOT_NEEDED),
    ],
)
def test_embedding_use_is_counted_for_this_question_only(
    monkeypatch, calls, embedding, expected_use
):
    embedder = FakeEmbedder(hit_count=7, miss_count=3)
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE], embedding))

    run = _asker(calls, embedder).ask("question", "pallets/flask", "3.1.3")

    assert run.embedding_use == expected_use


def test_no_sources_stops_before_the_answer_model(monkeypatch, calls):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, []))

    with pytest.raises(NoSourcesFoundError):
        _asker(calls).ask("question", "pallets/flask", "3.1.3")

    assert calls.generate_answer == []
    assert calls.rechecked_indexes == [VECTOR_INDEX_NAME]


@pytest.mark.parametrize(
    "search, question, expected_indexes",
    [
        ("vector", "How is the cookie signed?", [VECTOR_INDEX_NAME]),
        ("router", "url_for", [KEYWORD_INDEX_NAME]),
        ("router", "How is the cookie signed?", [VECTOR_INDEX_NAME, KEYWORD_INDEX_NAME]),
        ("hybrid", "url_for", [VECTOR_INDEX_NAME, KEYWORD_INDEX_NAME]),
    ],
)
def test_a_question_checks_only_the_indexes_its_search_uses(
    monkeypatch, calls, search, question, expected_indexes
):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE]))

    run = _asker(calls).ask(question, "pallets/flask", "3.1.3", search=search)

    assert calls.checked_indexes == expected_indexes
    assert calls.rechecked_indexes == []
    assert list(run.timings)[0] == "checks"


def test_options_reach_the_search_and_the_call_graph_check(monkeypatch, calls):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE]))
    expanded = []

    def expand_sources(chunks_collection, scorer, question, sources, repository, version):
        expanded.append(scorer)
        return asking.ExpandedSources(
            sources=sources,
            related_notes=[None],
            found_neighbor_count=0,
            kept_neighbors=[],
            timings={"expand": 1.0},
        )

    monkeypatch.setattr(asking, "expand_sources", expand_sources)
    asker = _asker(calls)

    asker.ask("question", "pallets/flask", "3.1.3", exclude_tests=True)
    run = asker.ask("question", "pallets/flask", "3.1.3", expand=True)

    assert calls.find_sources[0]["exclude_tests"] is True
    assert calls.find_sources[0]["collection"] == "chunks collection"
    assert calls.call_graph_checks == 1
    assert expanded == ["scorer"]
    assert run.timings["expand"] == 1.0


def test_a_version_indexed_before_license_paths_is_refused(monkeypatch, calls):
    monkeypatch.setattr(asking, "find_sources", _find_sources_returning(calls, [SOURCE]))

    class StoreWithoutLicensePath(FakeStore):
        def find_repository_record(self, repository, version):
            return {"commit_id": "c" * 40, "license_spdx_id": "BSD-3-Clause"}

    monkeypatch.setattr(asking, "MongoChunkStore", StoreWithoutLicensePath)

    with pytest.raises(SearchRefusedError, match="indexed before license files were recorded"):
        _asker(calls).ask("question", "pallets/flask", "3.1.3")

    assert calls.find_sources == []


@dataclass
class SearchCalls:
    vector_searches: list[dict] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=lambda: [SOURCE])


@pytest.fixture
def search_calls(monkeypatch, calls) -> SearchCalls:
    recorded = SearchCalls()

    def find_vector_results(chunks_collection, embedder, query, repository, version, options):
        recorded.vector_searches.append(
            {"collection": chunks_collection, "query": query, "options": options}
        )
        embedder.hit_count += 1
        return recorded.results, {"embed query": 30.0, "vector search": 45.0}

    monkeypatch.setattr(asking, "find_vector_results", find_vector_results)
    return recorded


def test_search_uses_vector_search_without_the_reranker_or_the_answer_model(calls, search_calls):
    run = _asker(calls).search(
        "how is the cookie signed", "pallets/flask", "3.1.3", limit=20, exclude_tests=True
    )

    assert run.results == [SOURCE]
    assert list(run.timings) == ["checks", "embed query", "vector search"]
    assert run.timings["embed query"] == 30.0
    assert run.timings["vector search"] == 45.0
    assert run.embedding_use == EmbeddingUse.FROM_CACHE
    assert run.repository_record == REPOSITORY_RECORD
    search = search_calls.vector_searches[0]
    assert search["collection"] == "chunks collection"
    assert search["query"] == "how is the cookie signed"
    assert search["options"].limit == 20
    assert search["options"].exclude_tests is True
    assert calls.scorer_loads == 0
    assert calls.generate_answer == []


def test_search_checks_only_the_vector_index(calls, search_calls):
    _asker(calls).search("query", "pallets/flask", "3.1.3")

    assert calls.checked_indexes == [VECTOR_INDEX_NAME]
    assert calls.rechecked_indexes == []


def test_a_search_that_finds_nothing_rechecks_the_vector_index(calls, search_calls):
    search_calls.results = []

    run = _asker(calls).search("query", "pallets/flask", "3.1.3")

    assert run.results == []
    assert calls.rechecked_indexes == [VECTOR_INDEX_NAME]


def test_search_defaults_to_ten_results_with_tests_included(calls, search_calls):
    _asker(calls).search("query", "pallets/flask", "3.1.3")

    options = search_calls.vector_searches[0]["options"]
    assert options.limit == 10
    assert options.exclude_tests is False


def test_search_refuses_a_version_indexed_before_license_paths(monkeypatch, calls, search_calls):
    class StoreWithoutLicensePath(FakeStore):
        def find_repository_record(self, repository, version):
            return {"commit_id": "c" * 40, "license_spdx_id": "BSD-3-Clause"}

    monkeypatch.setattr(asking, "MongoChunkStore", StoreWithoutLicensePath)

    with pytest.raises(SearchRefusedError, match="indexed before license files were recorded"):
        _asker(calls).search("query", "pallets/flask", "3.1.3")

    assert search_calls.vector_searches == []
