from __future__ import annotations

import pytest
from retrieval.answer_generation import AnswerSentence, Citation

from evaluation.faithfulness_inputs import (
    UNUSED_SCORE,
    AnswerRunFormatError,
    AnswerToJudge,
    ChangedSourceError,
    answers_to_judge,
    chunk_ids_of,
    sources_for,
)

COMMIT = "c" * 40

RUN_LOCATION = {
    "chunk_id": "pallets/flask@3.1.3:src/flask/app.py:10:function:1",
    "file_path": "src/flask/app.py",
    "qualified_name": "run",
    "kind": "function",
    "start_line": 10,
    "end_line": 12,
}

PREPARE_LOCATION = {
    "chunk_id": "pallets/flask@3.1.3:src/flask/app.py:20:function:1",
    "file_path": "src/flask/app.py",
    "qualified_name": "prepare",
    "kind": "function",
    "start_line": 20,
    "end_line": 21,
}


def _chunk(location: dict, text: str, commit_id: str = COMMIT) -> dict:
    return {
        "_id": location["chunk_id"],
        "file_path": location["file_path"],
        "qualified_name": location["qualified_name"],
        "kind": location["kind"],
        "start_line": location["start_line"],
        "end_line": location["end_line"],
        "signature": None,
        "part_number": 1,
        "part_count": 1,
        "is_test_file": False,
        "text": text,
        "commit_id": commit_id,
    }


CHUNKS_BY_ID = {
    RUN_LOCATION["chunk_id"]: _chunk(RUN_LOCATION, "def run():\n    prepare()\n    return 1"),
    PREPARE_LOCATION["chunk_id"]: _chunk(PREPARE_LOCATION, "def prepare():\n    pass"),
}


def _sentence_record(text: str, *citations: tuple[int, int, int]) -> dict:
    return {
        "text": text,
        "citations": [
            {"source_number": number, "start_line": start, "end_line": end}
            for number, start, end in citations
        ],
    }


def _setup_record(outcome: str = "answered", **overrides) -> dict:
    record = {
        "sources": [RUN_LOCATION, PREPARE_LOCATION],
        "outcome": outcome,
        "sentences": [_sentence_record("run calls prepare.", (1, 11, 11))],
    }
    record.update(overrides)
    return record


def _answer_run(*questions: dict) -> dict:
    return {"run": {}, "questions": list(questions)}


def _question(question_id: str, **setups: dict) -> dict:
    return {"id": question_id, "setups": setups}


def _answer(sentences: list[AnswerSentence]) -> AnswerToJudge:
    return AnswerToJudge(
        question_id="flask-001",
        setup="vector",
        sentences=sentences,
        source_locations=[RUN_LOCATION, PREPARE_LOCATION],
    )


def test_only_answered_replies_are_judged_in_question_then_setup_order():
    answer_run = _answer_run(
        _question("flask-001", vector=_setup_record(), **{"vector + expand": _setup_record()}),
        _question("flask-002", vector=_setup_record("said not found")),
        _question(
            "flask-003",
            vector=_setup_record("rejected", sentences=None),
            **{"vector + expand": _setup_record("failed")},
        ),
        _question("flask-004", vector=_setup_record()),
    )

    answers = answers_to_judge(answer_run)

    assert [(answer.question_id, answer.setup) for answer in answers] == [
        ("flask-001", "vector"),
        ("flask-001", "vector + expand"),
        ("flask-004", "vector"),
    ]
    assert answers[0].sentences == [
        AnswerSentence(text="run calls prepare.", citations=[Citation(1, 11, 11)])
    ]


def test_a_run_saved_without_sentences_is_refused():
    record = _setup_record()
    del record["sentences"]

    with pytest.raises(AnswerRunFormatError, match="run a new answer evaluation"):
        answers_to_judge(_answer_run(_question("flask-001", vector=record)))


def test_a_run_saved_without_chunk_ids_is_refused():
    location = {key: value for key, value in RUN_LOCATION.items() if key != "chunk_id"}
    record = _setup_record(sources=[location])

    with pytest.raises(AnswerRunFormatError):
        answers_to_judge(_answer_run(_question("flask-001", vector=record)))


def test_chunk_ids_are_listed_once_in_first_seen_order():
    first = _answer([])
    second = AnswerToJudge("flask-002", "vector", [], [PREPARE_LOCATION])

    assert chunk_ids_of([first, second]) == [
        RUN_LOCATION["chunk_id"],
        PREPARE_LOCATION["chunk_id"],
    ]


def test_sources_are_rebuilt_in_their_saved_order_with_the_stored_text():
    answer = _answer([AnswerSentence("run calls prepare.", [Citation(2, 20, 21)])])

    sources = sources_for(answer, CHUNKS_BY_ID, COMMIT)

    assert [source.qualified_name for source in sources] == ["run", "prepare"]
    assert sources[0].chunk_id == RUN_LOCATION["chunk_id"]
    assert sources[0].text == "def run():\n    prepare()\n    return 1"
    assert {source.score for source in sources} == {UNUSED_SCORE}


def test_a_missing_chunk_is_refused():
    answer = _answer([AnswerSentence("run calls prepare.", [Citation(1, 11, 11)])])
    chunks_by_id = {PREPARE_LOCATION["chunk_id"]: CHUNKS_BY_ID[PREPARE_LOCATION["chunk_id"]]}

    with pytest.raises(ChangedSourceError, match="No stored chunk"):
        sources_for(answer, chunks_by_id, COMMIT)


def test_a_chunk_from_another_commit_is_refused():
    answer = _answer([AnswerSentence("run calls prepare.", [Citation(1, 11, 11)])])

    with pytest.raises(ChangedSourceError, match="from commit"):
        sources_for(answer, CHUNKS_BY_ID, "d" * 40)


@pytest.mark.parametrize(
    "field_name, changed_value",
    [("end_line", 13), ("qualified_name", "Flask.run"), ("file_path", "src/flask/cli.py")],
)
def test_a_chunk_that_no_longer_matches_its_saved_location_is_refused(field_name, changed_value):
    changed_chunk = {**CHUNKS_BY_ID[RUN_LOCATION["chunk_id"]], field_name: changed_value}
    chunks_by_id = {**CHUNKS_BY_ID, RUN_LOCATION["chunk_id"]: changed_chunk}
    answer = _answer([AnswerSentence("run calls prepare.", [Citation(2, 20, 21)])])

    with pytest.raises(ChangedSourceError, match=field_name):
        sources_for(answer, chunks_by_id, COMMIT)


def test_a_citation_outside_its_source_is_refused_before_any_request():
    answer = _answer([AnswerSentence("run calls prepare.", [Citation(1, 11, 30)])])

    with pytest.raises(AnswerRunFormatError, match="outside source 1"):
        sources_for(answer, CHUNKS_BY_ID, COMMIT)


def test_an_answered_reply_that_cites_nothing_is_refused_before_any_request():
    answer = _answer([AnswerSentence("There are two steps.", [])])

    with pytest.raises(AnswerRunFormatError, match="cites nothing"):
        sources_for(answer, CHUNKS_BY_ID, COMMIT)
