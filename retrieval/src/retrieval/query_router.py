"""Send each question to the search that suits it: code names to keyword search, the rest to hybrid.

A vector captures meaning, and a sentence has plenty of it, but a bare name such as
`from_prefixed_env` has little: its vector is a weak summary of a few word pieces. Keyword search
matches the exact token instead, and boosts matches in a definition's name, so it is the better
tool for looking a name up.

The rule is deliberately simple. A question is a code name when, after removing surrounding
whitespace, backticks, and one trailing `()`, it is a single Python-style name made of ASCII
letters, digits, and underscores, not starting with a digit, optionally joined by dots, as in
`Config.from_object`. Plain single words such as `redirect` count too: keyword search handles one
word well, and one word has little meaning to embed. Anything containing a space goes to hybrid
search, so "where is url_for defined?" is not treated as a name; picking names out of sentences
is left out on purpose.

The router is no longer the default for answers. On the 50-question Flask evaluation (two answer
runs), plain vector search led it, so answers now use `vector_without_router` unless the router
is asked for. The router never chooses the vector route itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

CODE_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

BACKTICK = "`"

CALL_PARENTHESES = "()"


class QueryRoute(StrEnum):
    KEYWORD = "keyword"
    HYBRID = "hybrid"
    VECTOR = "vector"


@dataclass(frozen=True)
class RouteDecision:
    """Where a question goes, why, and the text to search with.

    For the keyword route, `query` is the bare name, without backticks or `()`; for the hybrid
    and vector routes, it is the question unchanged.
    """

    route: QueryRoute
    reason: str
    query: str


def route_query(question: str) -> RouteDecision:
    candidate_name = _bare_name_of(question)
    if CODE_NAME_PATTERN.fullmatch(candidate_name):
        return RouteDecision(
            route=QueryRoute.KEYWORD,
            reason="the question looks like a code name",
            query=candidate_name,
        )
    return RouteDecision(
        route=QueryRoute.HYBRID,
        reason="the question is not a single code name",
        query=question,
    )


def hybrid_without_router(question: str) -> RouteDecision:
    """The decision used when the router is turned off: every question goes to hybrid search."""
    return RouteDecision(
        route=QueryRoute.HYBRID, reason="the router is turned off", query=question
    )


def vector_without_router(question: str) -> RouteDecision:
    """The default decision for answers: every question goes to vector search."""
    return RouteDecision(
        route=QueryRoute.VECTOR, reason="every question goes to vector search", query=question
    )


def _bare_name_of(question: str) -> str:
    candidate = question.strip()
    if len(candidate) >= 2 and candidate.startswith(BACKTICK) and candidate.endswith(BACKTICK):
        candidate = candidate[1:-1].strip()
    candidate = candidate.removesuffix(CALL_PARENTHESES)
    return candidate
