from __future__ import annotations

from retrieval.answer_sources import ExpandedSources

from evaluation import search_setups
from evaluation.search_setups import SearchContext, SearchSetup, run_setups
from helpers import source

CONTEXT = SearchContext(
    chunks_collection=None,
    embedder=None,
    scorer=None,
    repository="owner/repo",
    version="1.0",
)


def _install_fakes(monkeypatch) -> list[str]:
    calls: list[str] = []

    def fake_vector(context, question):
        calls.append("vector")
        return [source("run", 10, 20)]

    def fake_router(context, question):
        calls.append("router")
        return [source("route", 30, 40)]

    def fake_expand(collection, scorer, question, sources, repository, version):
        calls.append("expand")
        neighbor = source("neighbor", 50, 60)
        return ExpandedSources(
            sources=[*sources, neighbor],
            related_notes=[*([None] * len(sources)), "called by source 1"],
            found_neighbor_count=1,
            kept_neighbors=[],
            timings={},
        )

    monkeypatch.setitem(search_setups.BASE_SEARCHES, SearchSetup.VECTOR, fake_vector)
    monkeypatch.setitem(search_setups.BASE_SEARCHES, SearchSetup.ROUTER, fake_router)
    monkeypatch.setattr(search_setups, "expand_sources", fake_expand)
    return calls


def test_only_the_requested_setups_run_and_expansion_reuses_its_base_run(monkeypatch):
    calls = _install_fakes(monkeypatch)

    runs = run_setups(
        CONTEXT, "question", [SearchSetup.ROUTER, SearchSetup.VECTOR, SearchSetup.VECTOR_EXPAND]
    )

    assert list(runs) == [SearchSetup.ROUTER, SearchSetup.VECTOR, SearchSetup.VECTOR_EXPAND]
    assert calls == ["router", "vector", "expand"]
    expanded = runs[SearchSetup.VECTOR_EXPAND]
    assert [result.qualified_name for result in expanded.sources] == ["run", "neighbor"]
    assert expanded.related_notes == [None, "called by source 1"]
    assert expanded.milliseconds >= runs[SearchSetup.VECTOR].milliseconds
    assert runs[SearchSetup.VECTOR].related_notes is None


def test_an_expand_setup_alone_runs_its_base_search_itself(monkeypatch):
    calls = _install_fakes(monkeypatch)

    runs = run_setups(CONTEXT, "question", [SearchSetup.VECTOR_EXPAND])

    assert list(runs) == [SearchSetup.VECTOR_EXPAND]
    assert calls == ["vector", "expand"]
