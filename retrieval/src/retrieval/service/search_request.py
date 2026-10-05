"""Checking the body of `POST /search` before any search runs.

The body must be a JSON object with exactly these fields: `repository`, `version`, `query` (by the
shared rules in `request_fields`), and optionally `exclude_tests` (true or false) and `limit`, the
number of results, a whole number from 1 to 20, 10 by default. The cap is 20 because every result
carries a snippet of up to 12 lines.

There is no search-mode option: search uses vector search, as answers do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval.config import DEFAULT_SEARCH_LIMIT
from retrieval.service.request_fields import (
    InvalidRequestError,
    boolean_field,
    repository_field,
    require_fields,
    text_field,
    version_field,
)

MAX_SERVICE_SEARCH_LIMIT = 20

REQUIRED_FIELDS = ("repository", "version", "query")

OPTIONAL_FIELDS = ("exclude_tests", "limit")


@dataclass(frozen=True)
class SearchRequest:
    repository: str
    version: str
    query: str
    exclude_tests: bool
    limit: int


def parse_search_request(body: Any) -> SearchRequest:
    fields = require_fields(body, REQUIRED_FIELDS, OPTIONAL_FIELDS)
    return SearchRequest(
        repository=repository_field(fields["repository"]),
        version=version_field(fields["version"]),
        query=text_field(fields["query"], "query"),
        exclude_tests=boolean_field(fields.get("exclude_tests", False), "exclude_tests"),
        limit=_limit(fields.get("limit", DEFAULT_SEARCH_LIMIT)),
    )


def _limit(value: Any) -> int:
    is_whole_number = isinstance(value, int) and not isinstance(value, bool)
    if not is_whole_number or not 1 <= value <= MAX_SERVICE_SEARCH_LIMIT:
        raise InvalidRequestError(
            f"limit must be a whole number from 1 to {MAX_SERVICE_SEARCH_LIMIT}"
        )
    return value
