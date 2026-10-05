from __future__ import annotations

import json

import pytest
from retrieval.answer_generation import AnswerSentence, Citation
from retrieval.search_results import SearchResult

from evaluation.faithfulness_judge import Verdict
from evaluation.hand_check import (
    ALLOWED_LABELS,
    Group,
    JudgedSentence,
    LabelsError,
    code_like_names,
    group_candidates,
    judged_sentences,
    key_document,
    labels_document,
    names_in_no_source,
    names_not_defined,
    render_worksheet,
    score_labels,
    select_items,
)
from evaluation.hand_check_results import render_hand_check_table

OPEN_SOURCE = SearchResult(
    chunk_id="open",
    file_path="src/flask/sessions.py",
    start_line=323,
    end_line=327,
    kind="method",
    qualified_name="SecureCookieSessionInterface.open_session",
    signature=None,
    part_number=1,
    part_count=1,
    is_test_file=False,
    text="    def open_session(self, app, request):\n"
    "        s = self.get_signing_serializer(app)\n"
    "        if s is None:\n"
    "            return None\n"
    "        val = request.cookies.get(self.get_cookie_name(app))",
    score=0.0,
)

SIGNING_SOURCE = SearchResult(
    chunk_id="signing",
    file_path="src/flask/sessions.py",
    start_line=303,
    end_line=305,
    kind="method",
    qualified_name="SecureCookieSessionInterface.get_signing_serializer",
    signature=None,
    part_number=1,
    part_count=1,
    is_test_file=False,
    text="    def get_signing_serializer(self, app):\n"
    "        return URLSafeTimedSerializer(app.secret_key)\n"
    "        data = json.loads(raw)",
    score=0.0,
)

SOURCES = [OPEN_SOURCE, SIGNING_SOURCE]

ANSWER_SENTENCES = [
    AnswerSentence("open_session returns None without a serializer.", [Citation(1, 325, 326)]),
    AnswerSentence(
        "It reads the cookie named by the SESSION_COOKIE_NAME setting.", [Citation(1, 327, 327)]
    ),
]


def _judged(
    question_id: str = "flask-001",
    sentence_number: int = 1,
    verdict: Verdict = Verdict.SUPPORTED,
    names_in_no_source: list[str] | None = None,
    setup: str = "vector",
) -> JudgedSentence:
    return JudgedSentence(
        question_id=question_id,
        setup=setup,
        sentence_number=sentence_number,
        text=f"Sentence {sentence_number} of {question_id}.",
        verdict=verdict,
        reason=f"Reason for {question_id} {sentence_number}.",
        names_in_no_source=names_in_no_source or [],
        names_not_defined=[],
    )


def _pool(count: int, verdict: Verdict, prefix: str, unshown: bool = False) -> list[JudgedSentence]:
    return [
        _judged(
            question_id=f"{prefix}-{number:03d}",
            verdict=verdict,
            names_in_no_source=["SOME_NAME"] if unshown else None,
        )
        for number in range(count)
    ]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("It calls get_cookie_name and reads SECRET_KEY.", ["get_cookie_name", "SECRET_KEY"]),
        ("Values pass through json.loads.", ["json.loads"]),
        ("It builds a URLSafeTimedSerializer.", ["URLSafeTimedSerializer"]),
        ("A SecureCookieSession or HTTPException.", ["SecureCookieSession", "HTTPException"]),
        ("Flask returns JSON for URLs, e.g. this one.", []),
        ("It reads FLASK_ variables, then FLASK_ again.", ["FLASK_"]),
    ],
)
def test_names_are_recognised_by_their_shape(text, expected):
    assert code_like_names(text) == expected


def test_a_name_in_no_source_is_one_no_source_text_or_location_contains():
    names = ["get_cookie_name", "SESSION_COOKIE_NAME", "json.loads", "sessions.py"]

    assert names_in_no_source(names, SOURCES) == ["SESSION_COOKIE_NAME"]


def test_a_name_not_defined_is_used_by_the_sources_but_defined_by_none():
    names = ["URLSafeTimedSerializer", "get_signing_serializer", "SESSION_COOKIE_NAME"]

    assert names_not_defined(names, SOURCES) == ["URLSafeTimedSerializer"]


def test_judged_sentences_take_text_and_names_from_the_answer():
    verdict_records = [
        {"sentence": 2, "reason": "Line 327 gets the cookie name.", "verdict": "supported"}
    ]

    judged = judged_sentences("flask-001", "vector", verdict_records, ANSWER_SENTENCES, SOURCES)

    assert judged == [
        JudgedSentence(
            question_id="flask-001",
            setup="vector",
            sentence_number=2,
            text="It reads the cookie named by the SESSION_COOKIE_NAME setting.",
            verdict=Verdict.SUPPORTED,
            reason="Line 327 gets the cookie name.",
            names_in_no_source=["SESSION_COOKIE_NAME"],
            names_not_defined=[],
        )
    ]


def test_every_judged_sentence_falls_in_exactly_one_group():
    sentences = [
        _judged("a", verdict=Verdict.UNSUPPORTED, names_in_no_source=["X_Y"]),
        _judged("b", verdict=Verdict.MISCITED),
        _judged("c", names_in_no_source=["X_Y"]),
        _judged("d"),
    ]

    groups = group_candidates(sentences)

    assert [sentence.question_id for sentence in groups[Group.NOT_SUPPORTED]] == ["a", "b"]
    assert [sentence.question_id for sentence in groups[Group.NAME_IN_NO_SOURCE]] == ["c"]
    assert [sentence.question_id for sentence in groups[Group.RANDOM_SUPPORTED]] == ["d"]


def test_groups_one_and_two_are_capped_and_group_three_fills_to_the_target():
    sentences = [
        *_pool(20, Verdict.UNSUPPORTED, "bad"),
        *_pool(5, Verdict.SUPPORTED, "unshown", unshown=True),
        *_pool(100, Verdict.SUPPORTED, "plain"),
    ]

    items = select_items(sentences, seed=7, target_count=40, group_cap=12)
    counts = {group: sum(1 for item in items if item.group == group) for group in Group}

    assert counts == {
        Group.NOT_SUPPORTED: 12,
        Group.NAME_IN_NO_SOURCE: 5,
        Group.RANDOM_SUPPORTED: 23,
    }
    assert [item.item_number for item in items] == list(range(1, 41))


def test_selection_is_the_same_for_the_same_seed_and_mixes_the_groups():
    sentences = [
        *_pool(20, Verdict.UNSUPPORTED, "bad"),
        *_pool(100, Verdict.SUPPORTED, "plain"),
    ]

    first = select_items(sentences, seed=7)
    second = select_items(sentences, seed=7)
    first_twelve_groups = {item.group for item in first[:12]}

    assert first == second
    assert first_twelve_groups == {Group.NOT_SUPPORTED, Group.RANDOM_SUPPORTED}


def test_the_worksheet_shows_the_sentence_and_code_but_never_the_verdict():
    judged = judged_sentences(
        "flask-001",
        "vector + expand",
        [{"sentence": 2, "reason": "Line 327 gets the cookie name.", "verdict": "supported"}],
        ANSWER_SENTENCES,
        SOURCES,
    )
    items = select_items(judged, seed=1)
    key = ("flask-001", "vector + expand")

    worksheet = render_worksheet(
        items, {key: ANSWER_SENTENCES}, {key: SOURCES}, "pallets/flask", "c" * 40
    )

    assert "**It reads the cookie named by the SESSION_COOKIE_NAME setting.**" in worksheet
    assert "open_session returns None without a serializer. **It reads" in worksheet
    assert "327|         val = request.cookies.get(self.get_cookie_name(app))" in worksheet
    assert (
        "https://github.com/pallets/flask/blob/" + "c" * 40 + "/src/flask/sessions.py#L303-L305"
    ) in worksheet
    assert "Line 327 gets the cookie name." not in worksheet
    assert "vector + expand" not in worksheet
    assert "flask-001" not in worksheet
    assert all(str(group) not in worksheet for group in Group)


def test_the_labels_file_holds_no_verdict_reason_group_or_setup():
    items = select_items([_judged(verdict=Verdict.UNSUPPORTED)], seed=1)

    labels = labels_document(items, "data/hand_checks/worksheet.md")
    labels_text = json.dumps(labels)

    assert labels["items"] == [
        {"item": 1, "sentence": "Sentence 1 of flask-001.", "label": None, "note": ""}
    ]
    assert "Reason for" not in labels_text
    assert "vector" not in labels_text
    assert "judged not supported" not in labels_text


def test_the_key_records_each_item_and_the_name_counts():
    sentences = [
        _judged("a", verdict=Verdict.UNSUPPORTED),
        _judged("b", names_in_no_source=["X_Y"]),
        _judged("c"),
    ]
    items = select_items(sentences, seed=1)

    key = key_document(items, sentences, seed=1)

    assert key["judged_sentence_count"] == 3
    assert key["sentences_with_a_name_in_no_source"] == 1
    assert key["group_sizes"] == {
        "judged not supported": 1,
        "names code no source shows": 1,
        "random supported": 1,
    }
    assert {entry["question_id"] for entry in key["items"]} == {"a", "b", "c"}


def _labels_and_key(pairs: list[tuple[str, str, str]]) -> tuple[dict, dict]:
    labels = {
        "items": [
            {"item": number, "sentence": f"Sentence {number}.", "label": label, "note": ""}
            for number, (_, _, label) in enumerate(pairs, start=1)
        ]
    }
    key = {
        "seed": 1,
        "judged_sentence_count": 10,
        "sentences_with_a_name_in_no_source": 2,
        "sentences_with_a_name_not_defined": 3,
        "group_sizes": {"judged not supported": 2, "random supported": 2},
        "items": [
            {"item": number, "group": group, "verdict": verdict, "reason": f"Reason {number}."}
            for number, (group, verdict, _) in enumerate(pairs, start=1)
        ],
    }
    return labels, key


def test_agreement_is_counted_per_group_both_ways():
    labels, key = _labels_and_key(
        [
            ("judged not supported", "unsupported", "unsupported"),
            ("judged not supported", "miscited", "unsupported"),
            ("random supported", "supported", "supported"),
            ("random supported", "supported", "unsupported"),
        ]
    )

    score = score_labels(labels, key)
    by_group = {agreement.group: agreement for agreement in score.agreements}

    assert by_group[Group.NOT_SUPPORTED].item_count == 2
    assert by_group[Group.NOT_SUPPORTED].supported_agreement_count == 2
    assert by_group[Group.NOT_SUPPORTED].exact_agreement_count == 1
    assert by_group[Group.RANDOM_SUPPORTED].supported_agreement_count == 1
    assert Group.NAME_IN_NO_SOURCE not in by_group
    assert score.label_by_verdict["unsupported"] == {
        "supported": 1,
        "miscited": 1,
        "unsupported": 1,
    }
    assert [disagreement.item_number for disagreement in score.disagreements] == [2, 4]


@pytest.mark.parametrize("bad_label", [None, "partly supported", "Supported"])
def test_an_unfinished_or_unknown_label_is_refused(bad_label):
    labels, key = _labels_and_key([("random supported", "supported", "supported")])
    labels["items"][0]["label"] = bad_label

    with pytest.raises(LabelsError, match="Items without a label"):
        score_labels(labels, key)


def test_labels_that_do_not_match_the_key_are_refused():
    labels, key = _labels_and_key([("random supported", "supported", "supported")])
    labels["items"][0]["item"] = 2

    with pytest.raises(LabelsError, match="different items"):
        score_labels(labels, key)


def test_the_table_reports_groups_the_label_table_and_disagreements():
    labels, key = _labels_and_key(
        [
            ("judged not supported", "unsupported", "unsupported"),
            ("random supported", "supported", "unsupported"),
        ]
    )
    labels["items"][1]["note"] = "The setting is not in the code."

    table = render_hand_check_table(
        score_labels(labels, key), key, "eval/results/x-handcheck-labels.json", "f.json"
    )

    assert "| judged not supported | 1 | 1/1 (100%) | 1/1 (100%) |" in table
    assert "| random supported | 1 | 0/1 (0%) | 0/1 (0%) |" in table
    assert f"| Hand label | {' | '.join(ALLOWED_LABELS)} |" in table
    assert "| unsupported | 1 | 0 | 1 |" in table
    assert "- Item 2 (random supported): judge supported, hand unsupported." in table
    assert "Note: The setting is not in the code." in table
