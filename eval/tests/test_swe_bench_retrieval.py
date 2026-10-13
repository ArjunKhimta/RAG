from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from retrieval.question_set import ExpectedDefinition
from retrieval.search_results import SearchResult

from evaluation.swe_bench_retrieval import (
    DepthScore,
    SweBenchTask,
    TaskFileError,
    load_tasks,
    render_table,
    score_results,
    summarize,
)

MODELS = "pkg/models.py"

UTILS = "pkg/utils.py"

COMMIT = "a" * 40


def result(file_path: str, qualified_name: str, start_line: int, end_line: int) -> SearchResult:
    return SearchResult(
        chunk_id=f"{file_path}:{start_line}",
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        kind="method",
        qualified_name=qualified_name,
        signature=None,
        part_number=1,
        part_count=1,
        is_test_file=False,
        text="def placeholder():\n    pass",
        score=0.5,
    )


def task(*expected: ExpectedDefinition, task_id: str = "org__pkg-1") -> SweBenchTask:
    return SweBenchTask(
        task_id=task_id,
        repository="org/pkg",
        base_commit=COMMIT,
        patch_files=(MODELS,),
        expected=expected,
        issue="Something breaks when sending a request.",
    )


SEND = ExpectedDefinition(MODELS, "Request.send", 40, 60)

PREPARE = ExpectedDefinition(MODELS, "Request.prepare", 70, 90)


class TestScoring:
    def test_ranks_the_first_result_in_a_changed_file_and_each_expected_definition(self):
        results = [
            result(UTILS, "helper", 1, 10),
            result(MODELS, "Request", 30, 100),
            result(MODELS, "Request.send", 40, 60),
        ]

        score = score_results(task(SEND, PREPARE), results, 5, 12.0)

        assert score.file_rank == 2
        assert score.definition_ranks == (3, None)
        assert score.definitions_found == 1
        assert score.reciprocal_rank == pytest.approx(1 / 3)

    def test_a_class_outline_does_not_count_as_its_method(self):
        score = score_results(task(SEND), [result(MODELS, "Request", 30, 100)], 5, 1.0)

        assert score.file_found
        assert score.definition_ranks == (None,)
        assert score.reciprocal_rank == 0.0

    def test_only_the_top_results_up_to_the_depth_count(self):
        results = [result(UTILS, f"helper_{index}", index, index) for index in range(5)]
        results.append(result(MODELS, "Request.send", 40, 60))

        at_five = score_results(task(SEND), results, 5, 1.0)
        at_ten = score_results(task(SEND), results, 10, 1.0)

        assert (at_five.file_rank, at_five.definition_ranks) == (None, (None,))
        assert (at_ten.file_rank, at_ten.definition_ranks) == (6, (6,))

    def test_the_first_found_definition_gives_the_reciprocal_rank(self):
        results = [
            result(MODELS, "Request.prepare", 70, 90),
            result(MODELS, "Request.send", 40, 60),
        ]

        score = score_results(task(SEND, PREPARE), results, 5, 1.0)

        assert score.first_definition_rank == 1
        assert score.reciprocal_rank == 1.0


class TestSummaries:
    def test_definitions_are_pooled_over_tasks(self):
        first = task(SEND, PREPARE, task_id="org__pkg-1")
        second = task(SEND, task_id="org__pkg-2")
        scores = [
            DepthScore("org__pkg-1", 5, 1, (1, None), 10.0),
            DepthScore("org__pkg-2", 5, None, (None,), 30.0),
        ]

        summary = summarize("vector", 5, [first, second], scores)

        assert (summary.files_found, summary.task_count) == (1, 2)
        assert (summary.definitions_found, summary.definitions_expected) == (1, 3)
        assert summary.definition_recall == pytest.approx(1 / 3)
        assert summary.mean_reciprocal_rank == pytest.approx(0.5)
        assert summary.median_milliseconds == 20.0

    def test_the_table_shows_each_setup_and_each_task(self):
        tasks = [task(SEND)]
        scores = {
            ("vector", 5): [DepthScore("org__pkg-1", 5, 2, (2,), 10.0)],
            ("vector", 10): [DepthScore("org__pkg-1", 10, 2, (2,), 11.0)],
        }
        summaries = [
            summarize(setup, depth, tasks, scores[(setup, depth)]) for setup, depth in scores
        ]

        table = render_table(["- details"], summaries, tasks, scores)

        assert "| vector | top 5 | 1/1 | 1/1 (100%) | 0.50 | 10 |" in table
        assert "| org__pkg-1 | Request.send | file #2, definition #2 |" in table
        assert "Something breaks" not in table
        assert "1 task from 1 repository: a small independent check" in table


class TestLoadingTasks:
    def write_files(self, folder: Path, recorded_sha: str | None = None) -> tuple[Path, Path]:
        tasks_path = folder / "tasks.json"
        rows = [
            {"instance_id": "org__pkg-1", "base_commit": COMMIT, "problem_statement": "Issue one"},
            {"instance_id": "org__pkg-2", "base_commit": COMMIT, "problem_statement": "Issue two"},
        ]
        tasks_path.write_text(json.dumps({"tasks": rows}))
        sha = recorded_sha or hashlib.sha256(tasks_path.read_bytes()).hexdigest()
        expected = [
            {
                "file_path": MODELS,
                "qualified_name": "Request.send",
                "start_line": 40,
                "end_line": 60,
            }
        ]
        items = [
            {
                "id": task_id,
                "repository": "org/pkg",
                "base_commit": COMMIT,
                "patch_files": [MODELS],
                "expected": expected,
            }
            for task_id in ("org__pkg-1", "org__pkg-2")
        ]
        question_path = folder / "questions.json"
        question_path.write_text(json.dumps({"tasks_file_sha256": sha, "tasks": items}))
        return question_path, tasks_path

    def test_joins_each_task_with_its_issue_text(self, tmp_path):
        question_path, tasks_path = self.write_files(tmp_path)

        _, tasks = load_tasks(question_path, tasks_path)

        assert [(item.task_id, item.issue) for item in tasks] == [
            ("org__pkg-1", "Issue one"),
            ("org__pkg-2", "Issue two"),
        ]
        assert tasks[0].expected == (SEND,)

    def test_refuses_a_tasks_file_other_than_the_recorded_one(self, tmp_path):
        question_path, tasks_path = self.write_files(tmp_path, recorded_sha="0" * 64)

        with pytest.raises(TaskFileError, match="not the one"):
            load_tasks(question_path, tasks_path)

    def test_keeps_only_the_chosen_tasks(self, tmp_path):
        question_path, tasks_path = self.write_files(tmp_path)

        _, tasks = load_tasks(question_path, tasks_path, only={"org__pkg-2"})

        assert [item.task_id for item in tasks] == ["org__pkg-2"]

    def test_refuses_an_unknown_task_id(self, tmp_path):
        question_path, tasks_path = self.write_files(tmp_path)

        with pytest.raises(TaskFileError, match="org__pkg-9"):
            load_tasks(question_path, tasks_path, only={"org__pkg-1", "org__pkg-9"})
