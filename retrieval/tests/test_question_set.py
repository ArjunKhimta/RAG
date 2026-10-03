from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from retrieval.answer_generation import Citation
from retrieval.question_set import (
    ExpectedDefinition,
    cited_definitions,
    found_definitions,
    is_covered_by,
    load_question_set,
)
from retrieval.search_results import SearchResult

CLI_FILE = "src/flask/cli.py"

LOCATE_APP = ExpectedDefinition(CLI_FILE, "locate_app", 241, 264)

FIND_BEST_APP = ExpectedDefinition(CLI_FILE, "find_best_app", 41, 91)

REPOSITORY_QUESTION_FILE = (
    Path(__file__).resolve().parents[2] / "eval" / "questions" / "pallets-flask-3.1.3.json"
)


def _source(qualified_name: str, start_line: int, end_line: int, kind: str = "function"):
    return SearchResult(
        chunk_id=f"{qualified_name}:{start_line}",
        file_path=CLI_FILE,
        start_line=start_line,
        end_line=end_line,
        kind=kind,
        qualified_name=qualified_name,
        signature=None,
        part_number=1,
        part_count=1,
        is_test_file=False,
        text="",
        score=1.0,
    )


def test_the_repository_question_file_loads_with_every_expected_definition(tmp_path):
    question_set = load_question_set(REPOSITORY_QUESTION_FILE)

    assert question_set.repository == "pallets/flask"
    assert question_set.version == "3.1.3"
    assert question_set.questions[8].question_id == "flask-009"
    assert question_set.questions[8].expected == (LOCATE_APP, FIND_BEST_APP)


def test_a_small_question_file_loads(tmp_path):
    question_file = tmp_path / "questions.json"
    question_file.write_text(
        json.dumps(
            {
                "repository": "owner/repo",
                "version": "1.0",
                "commit_id": "0" * 40,
                "questions": [
                    {
                        "id": "q-1",
                        "question": "Where is the app found?",
                        "query_style": "natural_language",
                        "expected": [
                            {
                                "file_path": CLI_FILE,
                                "qualified_name": "find_best_app",
                                "start_line": 41,
                                "end_line": 91,
                            }
                        ],
                    }
                ],
            }
        )
    )

    question_set = load_question_set(question_file)

    assert question_set.commit_id == "0" * 40
    assert question_set.questions[0].expected == (FIND_BEST_APP,)


def test_a_source_covers_a_definition_with_the_same_file_name_and_overlapping_lines():
    assert is_covered_by(LOCATE_APP, _source("locate_app", 241, 264))
    assert is_covered_by(LOCATE_APP, _source("locate_app", 250, 280))
    assert not is_covered_by(LOCATE_APP, _source("locate_app", 229, 232))
    assert not is_covered_by(LOCATE_APP, replace(_source("locate_app", 241, 264), file_path="x.py"))


def test_a_class_outline_spanning_a_method_does_not_cover_it():
    method = ExpectedDefinition(CLI_FILE, "ScriptInfo.load_app", 333, 377)

    assert not is_covered_by(method, _source("ScriptInfo", 293, 377, kind="class"))


def test_found_definitions_keeps_only_those_some_source_covers():
    sources = [_source("locate_app", 241, 264), _source("prepare_import", 200, 225)]

    assert found_definitions((LOCATE_APP, FIND_BEST_APP), sources) == [LOCATE_APP]


def test_a_definition_is_cited_only_when_a_citation_points_into_it():
    sources = [_source("locate_app", 241, 264), _source("find_best_app", 41, 91)]
    citations = [Citation(1, 260, 262), Citation(2, 95, 96)]

    assert cited_definitions((LOCATE_APP, FIND_BEST_APP), citations, sources) == [LOCATE_APP]


def test_a_citation_of_a_missing_source_cites_nothing():
    sources = [_source("locate_app", 241, 264)]

    assert cited_definitions((LOCATE_APP,), [Citation(3, 241, 250)], sources) == []
