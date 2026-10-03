from __future__ import annotations

from retrieval.call_graph_statistics import summarize_call_graph
from retrieval.call_sites import find_file_calls
from retrieval.chunker import chunk_python_source
from retrieval.code_graph import CallOutcome, build_call_graph, symbol_for

APP_FILE = "src/pkg/app.py"

APP_SOURCE = """\
import os
import typing as t


def helper():
    return 1


def first(page):
    helper()
    helper()
    return page.render()


def second(page):
    helper()
    os.getcwd()
    return second(page)


class Page:
    def render(self):
        return 1


class Card:
    def render(self):
        return 2

    @t.overload
    def size(self) -> int: ...

    def size(self):
        return 3
"""


def test_summary_counts_outcomes_edges_fan_in_ambiguity_and_repeated_symbols():
    source = APP_SOURCE.encode()
    chunks = chunk_python_source(APP_FILE, source)
    graph = build_call_graph(chunks, [find_file_calls(APP_FILE, source)])

    statistics = summarize_call_graph(graph, chunks)

    assert statistics.call_site_count == 6
    assert statistics.outcome_counts[CallOutcome.SAME_FILE] == 4
    assert statistics.outcome_counts[CallOutcome.AMBIGUOUS] == 1
    assert statistics.outcome_counts[CallOutcome.EXTERNAL] == 1
    assert statistics.resolved_count == 4
    assert statistics.self_call_count == 1
    assert statistics.edge_count == 2
    assert statistics.chunks_with_edges_count == 2
    assert statistics.caller_chunk_count == 7
    assert statistics.most_called == [(symbol_for(APP_FILE, "helper"), 2)]
    assert statistics.most_ambiguous_names == [("render", 1)]
    assert statistics.repeated_symbols == [(symbol_for(APP_FILE, "Card.size"), 2)]
