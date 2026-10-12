"""Checks on request body fields shared by `POST /ask` and `POST /search`.

Both routes name a repository version the same way, so they check it by the same rules:
`repository` as on GitHub (`owner/repo`), and `version` by the rule the cloner uses (a tag, a
branch without `/`, or a commit ID). The text to search with must be a string of at most 1,000
characters, not blank once trimmed. Unknown fields are refused rather than ignored, so a misspelt
option is noticed instead of silently doing nothing. Error messages name the field and the rule,
never echo the value sent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from retrieval.github_urls import OWNER_PATTERN, REPOSITORY_NAME_PATTERN, RESERVED_PATH_NAMES
from retrieval.repository_cloner import VERSION_PATTERN

MAX_TEXT_CHARACTERS = 1000


class InvalidRequestError(ValueError):
    """Raised when a request body breaks a rule; the message names the field."""


def require_fields(
    body: Any, required_fields: Sequence[str], optional_fields: Sequence[str]
) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise InvalidRequestError("The body must be a JSON object")
    unknown = sorted(set(body) - set(required_fields) - set(optional_fields))
    if unknown:
        raise InvalidRequestError(f"Unknown fields: {', '.join(unknown)}")
    missing = [field_name for field_name in required_fields if field_name not in body]
    if missing:
        raise InvalidRequestError(f"Missing fields: {', '.join(missing)}")
    return body


def repository_field(value: Any) -> str:
    rule = "repository must be owner/repo, as on GitHub"
    if not isinstance(value, str) or value.count("/") != 1:
        raise InvalidRequestError(rule)
    owner, name = value.split("/")
    is_valid_owner = OWNER_PATTERN.fullmatch(owner) is not None
    is_valid_name = REPOSITORY_NAME_PATTERN.fullmatch(name) is not None
    if not is_valid_owner or not is_valid_name or name in RESERVED_PATH_NAMES:
        raise InvalidRequestError(rule)
    return value.lower()


def version_field(value: Any) -> str:
    if not isinstance(value, str) or VERSION_PATTERN.fullmatch(value) is None:
        raise InvalidRequestError("version must be a tag, a branch without '/', or a commit ID")
    return value


def text_field(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f"{field_name} must be a non-blank string")
    text = value.strip()
    if len(text) > MAX_TEXT_CHARACTERS:
        raise InvalidRequestError(f"{field_name} must be at most {MAX_TEXT_CHARACTERS} characters")
    return text


def boolean_field(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidRequestError(f"{field_name} must be true or false")
    return value
