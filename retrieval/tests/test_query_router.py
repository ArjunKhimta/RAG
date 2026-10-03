from __future__ import annotations

import pytest

from retrieval.query_router import QueryRoute, route_query


@pytest.mark.parametrize(
    ("question", "expected_query"),
    [
        ("url_for", "url_for"),
        ("  url_for  ", "url_for"),
        ("`url_for`", "url_for"),
        ("`url_for()`", "url_for"),
        ("url_for()", "url_for"),
        ("Config.from_object", "Config.from_object"),
        ("MethodView", "MethodView"),
        ("_endpoint_from_view_func", "_endpoint_from_view_func"),
        ("__init__", "__init__"),
        ("redirect", "redirect"),
        ("FLASK_RUN_PORT", "FLASK_RUN_PORT"),
    ],
)
def test_a_single_code_name_goes_to_keyword_search_as_the_bare_name(question, expected_query):
    decision = route_query(question)

    assert decision.route == QueryRoute.KEYWORD
    assert decision.query == expected_query


@pytest.mark.parametrize(
    "question",
    [
        "How does url_for build URLs?",
        "where is url_for defined",
        "url_for redirect",
        "",
        "   ",
        "foo-bar",
        "1abc",
        "a.b.",
        ".hidden",
        "a..b",
        "url_for(endpoint)",
        "`url_for",
        "café",
    ],
)
def test_anything_else_goes_to_hybrid_search_unchanged(question):
    decision = route_query(question)

    assert decision.route == QueryRoute.HYBRID
    assert decision.query == question


def test_each_decision_says_why():
    assert route_query("url_for").reason == "the question looks like a code name"
    assert route_query("How?").reason == "the question is not a single code name"
