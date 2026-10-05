"""Checking the body of `POST /ask` before any search runs.

The body must be a JSON object with exactly these fields: `repository`, `version`, `question`
(by the shared rules in `request_fields`), and optionally `exclude_tests` (true or false).

The search mode and call-graph expansion are not options here: evaluation chose vector search
without expansion, and both alternatives would load the reranker's memory on a small server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval.service.request_fields import (
    boolean_field,
    repository_field,
    require_fields,
    text_field,
    version_field,
)

REQUIRED_FIELDS = ("repository", "version", "question")

OPTIONAL_FIELDS = ("exclude_tests",)


@dataclass(frozen=True)
class AskRequest:
    repository: str
    version: str
    question: str
    exclude_tests: bool


def parse_ask_request(body: Any) -> AskRequest:
    fields = require_fields(body, REQUIRED_FIELDS, OPTIONAL_FIELDS)
    return AskRequest(
        repository=repository_field(fields["repository"]),
        version=version_field(fields["version"]),
        question=text_field(fields["question"], "question"),
        exclude_tests=boolean_field(fields.get("exclude_tests", False), "exclude_tests"),
    )
