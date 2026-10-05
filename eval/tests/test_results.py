from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from retrieval.answer_generation import AnswerSentence, Citation

from evaluation.results import (
    RunDetails,
    group_summaries,
    project_commit_state,
    question_groups,
    render_retrieval_table,
    result_paths,
    sentence_records,
    source_location,
    write_json,
)
from evaluation.retrieval_metrics import QuestionScore
from helpers import expected_definition, question, source

RUN = expected_definition("run", 10, 20)

PREPARE = expected_definition("prepare", 30, 40)

DETAILS = RunDetails(
    run_at="2026-10-03T12:00:00+00:00",
    project_commit="a" * 40,
    project_has_uncommitted_changes=True,
    question_file="eval/questions/sample.json",
    question_file_sha256="b" * 64,
    repository="owner/repo",
    version="1.0",
    indexed_commit="c" * 40,
    embedding_model="embedding-model",
    embedding_dimensions=768,
    reranker_model="reranker-model",
    reranker_revision="d" * 40,
)


def _questions():
    return [
        question("q-1", (RUN,), origin="written_from_code"),
        question("q-2", (RUN, PREPARE)),
        question("q-3", (PREPARE,), query_style="identifier"),
    ]


def test_questions_are_grouped_by_origin_style_and_expected_count():
    groups = {group.label: group.question_ids for group in question_groups(_questions())}

    assert groups == {
        "all answerable": ["q-1", "q-2", "q-3"],
        "written from the code": ["q-1"],
        "from Stack Overflow": ["q-2", "q-3"],
        "name questions": ["q-3"],
        "plain-English questions": ["q-1", "q-2"],
        "one expected definition": ["q-1", "q-3"],
        "two or more expected definitions": ["q-2"],
    }


def test_each_group_is_summarized_per_setup_and_empty_groups_are_left_out():
    scores = {
        "router": [
            QuestionScore("q-1", 1, 1, 1, 10),
            QuestionScore("q-2", 2, 1, 2, 20),
            QuestionScore("q-3", 1, 1, 1, 30),
        ]
    }
    groups = [*question_groups(_questions()[:2])]

    summaries = group_summaries(groups, scores)

    assert "name questions" not in summaries
    assert summaries["all answerable"]["router"].recall == 2 / 3
    assert summaries["two or more expected definitions"]["router"].complete_count == 0


def test_the_table_shows_every_setup_overall_and_by_kind_of_question():
    scores = {
        "vector": [QuestionScore("q-1", 1, 0, None, 5), QuestionScore("q-2", 2, 1, 3, 7)],
        "router": [QuestionScore("q-1", 1, 1, 1, 9), QuestionScore("q-2", 2, 2, 1, 11)],
    }
    summaries = group_summaries(question_groups(_questions()[:2]), scores)

    table = render_retrieval_table(
        DETAILS, summaries, {"vector": 5, "router": 5}, skipped_ids=["q-9"], embedding_requests=2
    )

    assert "# Retrieval evaluation: owner/repo 1.0" in table
    assert "(with uncommitted changes)" in table
    assert "skipped, answer not in the code: q-9" in table
    assert "| vector | 5 | 33.3% (1/3) | 0/2 | 0.17 | 5 | 7 |" in table
    assert "| router | 5 | 100.0% (3/3) | 2/2 | 1.00 | 9 | 11 |" in table
    assert "| Setup | written from the code (1) | from Stack Overflow (1) |" in table
    assert "Embedding requests made by this run: 2" in table


def test_result_files_are_named_by_the_start_time(tmp_path):
    json_path, table_path = result_paths(tmp_path, datetime(2026, 10, 3, 14, 5, tzinfo=UTC))

    assert json_path.name == "2026-10-03-1405-retrieval.json"
    assert table_path.name == "2026-10-03-1405-retrieval.md"


def test_sentence_records_keep_each_sentence_with_its_own_citations():
    sentences = [
        AnswerSentence(text="Flask signs the cookie.", citations=[Citation(2, 407, 423)]),
        AnswerSentence(text="It uses the secret key.", citations=[]),
    ]

    assert sentence_records(sentences) == [
        {
            "text": "Flask signs the cookie.",
            "citations": [{"source_number": 2, "start_line": 407, "end_line": 423}],
        },
        {"text": "It uses the secret key.", "citations": []},
    ]


def test_saved_sources_hold_locations_but_never_code(tmp_path):
    location = source_location(source("run", 10, 20))
    path = tmp_path / "results" / "run.json"

    write_json(path, {"sources": [location]})

    assert json.loads(path.read_text()) == {
        "sources": [
            {
                "chunk_id": "run:10",
                "file_path": "src/pkg/app.py",
                "qualified_name": "run",
                "kind": "function",
                "start_line": 10,
                "end_line": 20,
            }
        ]
    }


def test_the_project_commit_and_uncommitted_changes_are_read_from_git(tmp_path):
    git = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com"]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "file.txt").write_text("one\n")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "first"], cwd=tmp_path, check=True)

    commit, has_changes = project_commit_state(tmp_path)
    (tmp_path / "file.txt").write_text("two\n")
    _, has_changes_after_edit = project_commit_state(tmp_path)

    assert len(commit) == 40
    assert not has_changes
    assert has_changes_after_edit
