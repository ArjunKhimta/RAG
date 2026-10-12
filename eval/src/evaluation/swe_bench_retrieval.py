"""Scoring search on the SWE-bench Lite subset: does it find the code each task's real fix changed?

Each task's issue text is the query, searched in the repository at the task's base commit. A
setup's results are scored at two depths, its top 5 (what the answer model would see) and its top
10 (what a person searching would scan), each from its own search:
- file found: some result is in a file the fix changed; its rank is the first such result's
- definitions found: expected definitions covered by some result (same file and qualified name,
  overlapping lines, as for the handwritten questions); each one's rank is the first result
  covering it, and the first of those ranks gives the reciprocal rank
Summaries pool definitions over tasks, as the handwritten evaluation pools them over questions.

The setups are the shipped default (vector search, tests included), vector search with test files
left out, keyword search, and hybrid search. Reranking is left out: the cross-encoder refuses
queries over 256 tokens, and issues are longer.

The issue text and patch are never saved in results; they come from the tasks file, checked
against the SHA-256 the question file records, so a result is never computed from other text.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pymongo.collection import Collection
from retrieval.embedders import QueryEmbedder
from retrieval.hybrid_search import HybridSearchOptions, hybrid_search
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.question_set import ExpectedDefinition, is_covered_by
from retrieval.search_results import SearchResult
from retrieval.vector_search import SearchOptions, search_chunks

DEPTHS = (5, 10)

MILLISECONDS_PER_SECOND = 1000

PERCENT = 100


class SweBenchSetup(StrEnum):
    VECTOR = "vector"
    VECTOR_WITHOUT_TESTS = "vector, tests left out"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


class TaskFileError(ValueError):
    """Raised when the tasks file does not match the question file or lacks a task."""


@dataclass(frozen=True)
class SweBenchTask:
    task_id: str
    repository: str
    base_commit: str
    patch_files: tuple[str, ...]
    expected: tuple[ExpectedDefinition, ...]
    issue: str


@dataclass(frozen=True)
class SearchContext:
    chunks_collection: Collection
    embedder: QueryEmbedder


@dataclass(frozen=True)
class DepthScore:
    """How one setup's top `depth` results did on one task. Ranks are 1-indexed; None is a miss."""

    task_id: str
    depth: int
    file_rank: int | None
    definition_ranks: tuple[int | None, ...]
    milliseconds: float

    @property
    def file_found(self) -> bool:
        return self.file_rank is not None

    @property
    def definitions_found(self) -> int:
        return sum(1 for rank in self.definition_ranks if rank is not None)

    @property
    def first_definition_rank(self) -> int | None:
        found = [rank for rank in self.definition_ranks if rank is not None]
        return min(found) if found else None

    @property
    def reciprocal_rank(self) -> float:
        rank = self.first_definition_rank
        return 0.0 if rank is None else 1 / rank


@dataclass(frozen=True)
class DepthSummary:
    setup: str
    depth: int
    task_count: int
    files_found: int
    definitions_found: int
    definitions_expected: int
    mean_reciprocal_rank: float
    median_milliseconds: float

    @property
    def definition_recall(self) -> float:
        if self.definitions_expected == 0:
            return 0.0
        return self.definitions_found / self.definitions_expected


def load_tasks(
    question_path: Path, tasks_path: Path, only: set[str] | None = None
) -> tuple[dict[str, Any], list[SweBenchTask]]:
    """Return the question file and its tasks with their issue text, checked against its SHA-256."""
    question_document = json.loads(question_path.read_text(encoding="utf-8"))
    tasks_bytes = tasks_path.read_bytes()
    if hashlib.sha256(tasks_bytes).hexdigest() != question_document["tasks_file_sha256"]:
        raise TaskFileError("the tasks file is not the one the question file was written from")
    rows = {row["instance_id"]: row for row in json.loads(tasks_bytes)["tasks"]}
    wanted = [item for item in question_document["tasks"] if only is None or item["id"] in only]
    if only is not None and len(wanted) != len(only):
        unknown = sorted(only - {item["id"] for item in wanted})
        raise TaskFileError(f"unknown task IDs: {', '.join(unknown)}")
    tasks = []
    for item in wanted:
        row = rows.get(item["id"])
        if row is None or row["base_commit"] != item["base_commit"]:
            raise TaskFileError(f"the tasks file has no matching row for {item['id']}")
        tasks.append(_task_from(item, row["problem_statement"]))
    return question_document, tasks


def run_search(
    context: SearchContext, setup: SweBenchSetup, task: SweBenchTask, depth: int
) -> tuple[list[SearchResult], float]:
    """Search one task's base commit with its issue text; return the results and milliseconds."""
    started = time.perf_counter()
    results = _search(context, setup, task, depth)
    return results, (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def score_results(
    task: SweBenchTask, results: list[SearchResult], depth: int, milliseconds: float
) -> DepthScore:
    top = results[:depth]
    return DepthScore(
        task_id=task.task_id,
        depth=depth,
        file_rank=_first_rank(top, lambda result: result.file_path in task.patch_files),
        definition_ranks=tuple(
            _first_rank(top, lambda result, expected=expected: is_covered_by(expected, result))
            for expected in task.expected
        ),
        milliseconds=milliseconds,
    )


def summarize(
    setup: str, depth: int, tasks: list[SweBenchTask], scores: list[DepthScore]
) -> DepthSummary:
    return DepthSummary(
        setup=setup,
        depth=depth,
        task_count=len(scores),
        files_found=sum(1 for score in scores if score.file_found),
        definitions_found=sum(score.definitions_found for score in scores),
        definitions_expected=sum(len(task.expected) for task in tasks),
        mean_reciprocal_rank=statistics.fmean(score.reciprocal_rank for score in scores),
        median_milliseconds=statistics.median(score.milliseconds for score in scores),
    )


def render_table(
    detail_lines: list[str],
    summaries: list[DepthSummary],
    tasks: list[SweBenchTask],
    scores: dict[tuple[str, int], list[DepthScore]],
) -> str:
    lines = ["# SWE-bench Lite subset: retrieval", "", *detail_lines, "", "## All tasks", ""]
    lines.append(_row(["Setup", "Depth", "File found", "Definitions found", "MRR", "Median ms"]))
    lines.append(_row(["---"] * 6))
    for summary in summaries:
        lines.append(
            _row(
                [
                    summary.setup,
                    f"top {summary.depth}",
                    f"{summary.files_found}/{summary.task_count}",
                    f"{summary.definitions_found}/{summary.definitions_expected} "
                    f"({summary.definition_recall * PERCENT:.0f}%)",
                    f"{summary.mean_reciprocal_rank:.2f}",
                    f"{summary.median_milliseconds:.0f}",
                ]
            )
        )
    setups = list(dict.fromkeys(setup for setup, _ in scores))
    deepest = max(DEPTHS)
    lines += ["", f"## Each task, top {deepest}", ""]
    lines.append(_row(["Task", "Expected", *setups]))
    lines.append(_row(["---"] * (2 + len(setups))))
    for position, task in enumerate(tasks):
        cells = [_task_cell(scores[(setup, deepest)][position]) for setup in setups]
        names = ", ".join(expected.qualified_name for expected in task.expected)
        lines.append(_row([task.task_id, names, *cells]))
    lines += ["", *_measure_notes()]
    return "\n".join(lines) + "\n"


def _task_from(item: dict[str, Any], issue: str) -> SweBenchTask:
    return SweBenchTask(
        task_id=item["id"],
        repository=item["repository"],
        base_commit=item["base_commit"],
        patch_files=tuple(item["patch_files"]),
        expected=tuple(
            ExpectedDefinition(
                file_path=expected["file_path"],
                qualified_name=expected["qualified_name"],
                start_line=expected["start_line"],
                end_line=expected["end_line"],
            )
            for expected in item["expected"]
        ),
        issue=issue,
    )


def _search(
    context: SearchContext, setup: SweBenchSetup, task: SweBenchTask, depth: int
) -> list[SearchResult]:
    if setup in (SweBenchSetup.VECTOR, SweBenchSetup.VECTOR_WITHOUT_TESTS):
        query_vector = context.embedder.embed_query(task.issue)
        options = SearchOptions(
            limit=depth, exclude_tests=setup == SweBenchSetup.VECTOR_WITHOUT_TESTS
        )
        return search_chunks(
            context.chunks_collection, query_vector, task.repository, task.base_commit, options
        )
    if setup == SweBenchSetup.KEYWORD:
        return search_chunks_by_keywords(
            context.chunks_collection,
            task.issue,
            task.repository,
            task.base_commit,
            KeywordSearchOptions(limit=depth),
        )
    outcome = hybrid_search(
        context.chunks_collection,
        context.embedder,
        task.issue,
        task.repository,
        task.base_commit,
        HybridSearchOptions(limit=depth),
    )
    return [fused.result for fused in outcome.results]


def _first_rank(results: list[SearchResult], matches: Any) -> int | None:
    for rank, result in enumerate(results, start=1):
        if matches(result):
            return rank
    return None


def _task_cell(score: DepthScore) -> str:
    file_part = "file —" if score.file_rank is None else f"file #{score.file_rank}"
    ranks = ["—" if rank is None else f"#{rank}" for rank in score.definition_ranks]
    return f"{file_part}, definition {' '.join(ranks)}"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _measure_notes() -> list[str]:
    return [
        "File found: tasks with a result in a file the fix changed, among the top results. "
        "Definitions found: expected definitions (the functions or classes holding the fix's "
        "changed lines at the base commit) covered by a result, pooled over tasks. MRR: mean of "
        "1 / the rank of the first result covering an expected definition (0 when none). Each "
        "depth is its own search. Times are wall-clock per task for the whole search, with the "
        "issue's embedding from the cache after its first use. Each task's cell gives the rank of "
        "the first result in a changed file and of each expected definition; — is not found. "
        "Nine tasks from two repositories: a small independent check, not a benchmark score.",
    ]
