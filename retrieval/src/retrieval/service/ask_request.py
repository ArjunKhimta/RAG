"""Checking the body of `POST /ask` before any search runs.

The body must be a JSON object with exactly these fields: `repository` (`owner/repo`, by the same
rules as GitHub URLs), `version` (by the same rule the cloner uses: a tag, a branch without `/`,
or a commit ID), `question` (a string of at most 1,000 characters, not blank once trimmed), and
optionally `exclude_tests` (true or false). Unknown fields are refused rather than ignored, so a
misspelt option is noticed instead of silently doing nothing. Error messages name the field and
the rule, never echo the value sent.

The search mode and call-graph expansion are not options here: evaluation chose vector search
without expansion, and both alternatives would load the reranker's memory on a small server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval.github_urls import OWNER_PATTERN, REPOSITORY_NAME_PATTERN, RESERVED_PATH_NAMES
from retrieval.repository_cloner import VERSION_PATTERN

MAX_QUESTION_CHARACTERS = 1000

REQUIRED_FIELDS = ("repository", "version", "question")

OPTIONAL_FIELDS = ("exclude_tests",)


class InvalidAskRequestError(ValueError):
    """Raised when the body of `POST /ask` breaks a rule; the message names the field."""


@dataclass(frozen=True)
class AskRequest:
    repository: str
    version: str
    question: str
    exclude_tests: bool


def parse_ask_request(body: Any) -> AskRequest:
    if not isinstance(body, dict):
        raise InvalidAskRequestError("The body must be a JSON object")
    unknown = sorted(set(body) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise InvalidAskRequestError(f"Unknown fields: {', '.join(unknown)}")
    missing = [field_name for field_name in REQUIRED_FIELDS if field_name not in body]
    if missing:
        raise InvalidAskRequestError(f"Missing fields: {', '.join(missing)}")
    return AskRequest(
        repository=_repository(body["repository"]),
        version=_version(body["version"]),
        question=_question(body["question"]),
        exclude_tests=_exclude_tests(body.get("exclude_tests", False)),
    )


def _repository(value: Any) -> str:
    rule = "repository must be owner/repo, as on GitHub"
    if not isinstance(value, str) or value.count("/") != 1:
        raise InvalidAskRequestError(rule)
    owner, name = value.split("/")
    is_valid_owner = OWNER_PATTERN.fullmatch(owner) is not None
    is_valid_name = REPOSITORY_NAME_PATTERN.fullmatch(name) is not None
    if not is_valid_owner or not is_valid_name or name in RESERVED_PATH_NAMES:
        raise InvalidAskRequestError(rule)
    return value


def _version(value: Any) -> str:
    if not isinstance(value, str) or VERSION_PATTERN.fullmatch(value) is None:
        raise InvalidAskRequestError("version must be a tag, a branch without '/', or a commit ID")
    return value


def _question(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidAskRequestError("question must be a non-blank string")
    question = value.strip()
    if len(question) > MAX_QUESTION_CHARACTERS:
        raise InvalidAskRequestError(
            f"question must be at most {MAX_QUESTION_CHARACTERS} characters"
        )
    return question


def _exclude_tests(value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidAskRequestError("exclude_tests must be true or false")
    return value
