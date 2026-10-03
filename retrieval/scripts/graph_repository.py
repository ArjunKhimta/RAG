"""Clone a GitHub repository at a version, build its call graph, and print call graph statistics.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/graph_repository.py https://github.com/pallets/flask --version 3.1.3

An existing clone of that version is reused. Only source files take part; test files are left out.
Nothing is written to MongoDB and no model is called. Exits 0 on success and 1 when the URL, the
version, or the repository is refused. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time

from retrieval.call_graph_statistics import CallGraphStatistics, summarize_call_graph
from retrieval.code_graph import RESOLVED_OUTCOMES, CallOutcome, build_call_graph
from retrieval.github_urls import InvalidRepositoryUrlError, parse_github_url
from retrieval.redaction import redact
from retrieval.repository_cloner import ClonedRepository, RepositoryError, clone_repository
from retrieval.repository_walker import chunk_source_files, find_python_files

OUTCOME_COLUMN_WIDTH = 32

COUNT_COLUMN_WIDTH = 7

MILLISECONDS_PER_SECOND = 1000

PERCENT = 100


def main() -> int:
    arguments = _parse_arguments()
    try:
        reference = parse_github_url(arguments.url)
        clone_started = time.perf_counter()
        cloned = clone_repository(reference, arguments.version)
        clone_milliseconds = _milliseconds_since(clone_started)
    except (InvalidRepositoryUrlError, RepositoryError) as error:
        _print(f"Refused: {error}")
        return 1
    walk_started = time.perf_counter()
    walk_result = find_python_files(cloned.source_path)
    walk_milliseconds = _milliseconds_since(walk_started)
    chunk_started = time.perf_counter()
    chunking = chunk_source_files(walk_result.files)
    chunk_milliseconds = _milliseconds_since(chunk_started)
    resolve_started = time.perf_counter()
    graph = build_call_graph(chunking.chunks, chunking.file_calls)
    resolve_milliseconds = _milliseconds_since(resolve_started)
    statistics = summarize_call_graph(graph, chunking.chunks)
    report_lines = [
        *_repository_lines(cloned, len(chunking.file_calls)),
        *_outcome_lines(statistics),
        *_edge_lines(statistics),
        *_ranked_lines("Most called (distinct calling definitions)", statistics.most_called),
        *_ranked_lines("Most common ambiguous names (call sites)", statistics.most_ambiguous_names),
        *_ranked_lines("Symbols defined more than once in a file", statistics.repeated_symbols),
        *_timing_lines(
            cloned, clone_milliseconds, walk_milliseconds, chunk_milliseconds, resolve_milliseconds
        ),
    ]
    for line in report_lines:
        _print(line)
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="https://github.com/<owner>/<repo>")
    parser.add_argument("--version", required=True, help="tag, branch, or commit ID to clone")
    return parser.parse_args()


def _repository_lines(cloned: ClonedRepository, source_file_count: int) -> list[str]:
    metadata = cloned.metadata
    return [
        f"Repository  {metadata.repository} at {metadata.version}",
        f"Commit      {metadata.commit_id}",
        f"License     {metadata.license_spdx_id} ({metadata.license_name})",
        f"Source files in the graph: {source_file_count} (test files left out)",
        "",
    ]


def _outcome_lines(statistics: CallGraphStatistics) -> list[str]:
    lines = [f"Call sites in source functions and methods: {statistics.call_site_count}"]
    lines.append("  Resolved")
    for outcome in CallOutcome:
        if outcome in RESOLVED_OUTCOMES:
            lines.append(_outcome_row(outcome, statistics))
    lines.append(_total_row("resolved total", statistics.resolved_count, statistics))
    lines.append("  Unresolved")
    for outcome in CallOutcome:
        if outcome not in RESOLVED_OUTCOMES:
            lines.append(_outcome_row(outcome, statistics))
    unresolved_count = statistics.call_site_count - statistics.resolved_count
    lines.append(_total_row("unresolved total", unresolved_count, statistics))
    lines.append("")
    return lines


def _edge_lines(statistics: CallGraphStatistics) -> list[str]:
    return [
        f"Edges (distinct chunk to callee pairs): {statistics.edge_count}",
        f"Calls from a definition to itself (no edge): {statistics.self_call_count}",
        f"Function and method chunks with at least one edge: "
        f"{statistics.chunks_with_edges_count} of {statistics.caller_chunk_count}",
        "",
    ]


def _ranked_lines(title: str, ranked: list[tuple[str, int]]) -> list[str]:
    lines = [title]
    if not ranked:
        lines.append("  none")
    for name, count in ranked:
        lines.append(f"  {count:5}  {name}")
    lines.append("")
    return lines


def _timing_lines(
    cloned: ClonedRepository,
    clone_milliseconds: float,
    walk_milliseconds: float,
    chunk_milliseconds: float,
    resolve_milliseconds: float,
) -> list[str]:
    clone_label = "reuse clone" if cloned.was_reused else "clone"
    return [
        "Timing",
        f"  {clone_label.ljust(22)}{clone_milliseconds:8.0f} ms",
        f"  {'walk'.ljust(22)}{walk_milliseconds:8.0f} ms",
        f"  {'scan, chunk, calls'.ljust(22)}{chunk_milliseconds:8.0f} ms",
        f"  {'resolve'.ljust(22)}{resolve_milliseconds:8.0f} ms",
    ]


def _outcome_row(outcome: CallOutcome, statistics: CallGraphStatistics) -> str:
    return _total_row(str(outcome), statistics.outcome_counts[outcome], statistics)


def _total_row(label: str, count: int, statistics: CallGraphStatistics) -> str:
    share = 0.0
    if statistics.call_site_count:
        share = count / statistics.call_site_count * PERCENT
    return (
        f"    {label.ljust(OUTCOME_COLUMN_WIDTH)}"
        f"{str(count).rjust(COUNT_COLUMN_WIDTH)}  {share:5.1f}%"
    )


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
