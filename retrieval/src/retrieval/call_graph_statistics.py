"""Summary statistics over a repository's call graph, used to judge how much the resolver finds.

Fan-in counts distinct calling definitions, not call sites, so a function that calls `helper()`
five times adds one to `helper`'s count. Repeated symbols are names defined more than once in the
same file, such as `typing.overload` stubs or a property with its setter: their chunks share one
symbol, so an edge to them cannot say which definition it means.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.code_graph import (
    CALLER_KINDS,
    RESOLVED_OUTCOMES,
    CallGraph,
    CallOutcome,
    symbol_for,
)

LIST_LENGTH = 10


@dataclass(frozen=True)
class CallGraphStatistics:
    call_site_count: int
    outcome_counts: Counter[CallOutcome]
    resolved_count: int
    self_call_count: int
    edge_count: int
    caller_chunk_count: int
    chunks_with_edges_count: int
    most_called: list[tuple[str, int]]
    most_ambiguous_names: list[tuple[str, int]]
    repeated_symbols: list[tuple[str, int]]


def summarize_call_graph(graph: CallGraph, chunks: list[CodeChunk]) -> CallGraphStatistics:
    source_chunks = [chunk for chunk in chunks if not chunk.is_test_file]
    resolved_calls = graph.resolved_calls
    outcome_counts = Counter(resolved_call.outcome for resolved_call in resolved_calls)
    self_call_count = sum(
        1
        for resolved_call in resolved_calls
        if resolved_call.callee_symbol is not None
        and resolved_call.callee_symbol == resolved_call.caller_symbol
    )
    ambiguous_names = Counter(
        resolved_call.call_site.called_name
        for resolved_call in resolved_calls
        if resolved_call.outcome == CallOutcome.AMBIGUOUS
        and resolved_call.call_site.called_name is not None
    )
    return CallGraphStatistics(
        call_site_count=len(resolved_calls),
        outcome_counts=outcome_counts,
        resolved_count=sum(outcome_counts[outcome] for outcome in RESOLVED_OUTCOMES),
        self_call_count=self_call_count,
        edge_count=sum(len(callees) for callees in graph.callees_by_chunk.values()),
        caller_chunk_count=sum(1 for chunk in source_chunks if chunk.kind in CALLER_KINDS),
        chunks_with_edges_count=len(graph.callees_by_chunk),
        most_called=_most_called(graph),
        most_ambiguous_names=_largest_first(ambiguous_names),
        repeated_symbols=_repeated_symbols(source_chunks),
    )


def _most_called(graph: CallGraph) -> list[tuple[str, int]]:
    callers_by_callee: dict[str, set[str]] = defaultdict(set)
    for chunk, callee_symbols in graph.callees_by_chunk.items():
        caller_symbol = symbol_for(chunk.file_path, chunk.qualified_name)
        for callee_symbol in callee_symbols:
            callers_by_callee[callee_symbol].add(caller_symbol)
    fan_in = Counter({callee: len(callers) for callee, callers in callers_by_callee.items()})
    return _largest_first(fan_in)


def _repeated_symbols(source_chunks: list[CodeChunk]) -> list[tuple[str, int]]:
    """Return symbols defined more than once in a file, counting first parts only."""
    definition_counts = Counter(
        symbol_for(chunk.file_path, chunk.qualified_name)
        for chunk in source_chunks
        if chunk.kind != ChunkKind.MODULE and chunk.part_number == 1
    )
    repeated = Counter({symbol: count for symbol, count in definition_counts.items() if count > 1})
    return _largest_first(repeated, limit=None)


def _largest_first(counts: Counter[str], limit: int | None = LIST_LENGTH) -> list[tuple[str, int]]:
    """Sort by count, largest first, then by name, so ties always print in the same order."""
    ordered = sorted(counts.items(), key=_largest_count_then_name)
    if limit is None:
        return ordered
    return ordered[:limit]


def _largest_count_then_name(item: tuple[str, int]) -> tuple[int, str]:
    name, count = item
    return -count, name
