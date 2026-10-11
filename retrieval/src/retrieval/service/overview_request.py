"""Checking the body of `POST /overview`: exactly `repository` and `version`, by the shared rules
in `request_fields`, with unknown fields refused."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval.service.request_fields import repository_field, require_fields, version_field

REQUIRED_FIELDS = ("repository", "version")


@dataclass(frozen=True)
class OverviewRequest:
    repository: str
    version: str


def parse_overview_request(body: Any) -> OverviewRequest:
    fields = require_fields(body, REQUIRED_FIELDS, ())
    return OverviewRequest(
        repository=repository_field(fields["repository"]),
        version=version_field(fields["version"]),
    )
