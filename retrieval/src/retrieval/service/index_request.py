"""Checking the body of `POST /index` before any job starts.

The body must be a JSON object with exactly `repository` and `version`, by the shared rules in
`request_fields`. Unknown fields are refused, so a misspelt option is noticed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval.service.request_fields import repository_field, require_fields, version_field

REQUIRED_FIELDS = ("repository", "version")


@dataclass(frozen=True)
class IndexRequest:
    repository: str
    version: str


def parse_index_request(body: Any) -> IndexRequest:
    fields = require_fields(body, REQUIRED_FIELDS, ())
    return IndexRequest(
        repository=repository_field(fields["repository"]),
        version=version_field(fields["version"]),
    )
