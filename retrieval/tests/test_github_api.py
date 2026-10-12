"""Tests for the one GitHub API call: the optional token, rate-limit refusals, and redirects.

A stand-in opener records each request and answers with JSON or an HTTP error, so no test
reaches GitHub.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.request

import pytest

from retrieval import repository_cloner
from retrieval.github_urls import RepositoryReference
from retrieval.indexing_jobs import build_indexing_job_runner
from retrieval.redaction import clear_registered_secrets, redact
from retrieval.repository_cloner import (
    GITHUB_RATE_LIMITED_MESSAGE,
    GitHubRateLimitedError,
    InvalidGitHubTokenError,
    RepositoryError,
    RepositoryNotFoundError,
    TokenDroppingRedirectHandler,
    clone_repository,
    fetch_github_repository_details,
    read_github_api_token,
)

REFERENCE = RepositoryReference(owner="pallets", name="flask")

FINE_GRAINED_TOKEN = "github_pat_11AAAAAAA0exampleexampleexample"

CLASSIC_TOKEN = "ghp_exampleexampleexampleexample0000"

REPOSITORY_JSON = {
    "private": False,
    "size": 2048,
    "license": {"spdx_id": "BSD-3-Clause", "name": "BSD 3-Clause License"},
}


class StandInOpener:
    """Answers each call with the next outcome: a dict becomes JSON, an exception is raised."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(json.dumps(outcome).encode())

    def authorization_headers(self) -> list[str | None]:
        return [request.get_header("Authorization") for request in self.requests]


def http_error(code: int, reason: str, headers: dict[str, str] | None = None) -> Exception:
    return urllib.error.HTTPError(
        "https://api.github.com/repos/pallets/flask", code, reason, headers or {}, None
    )


@pytest.fixture(autouse=True)
def forget_registered_secrets():
    yield
    clear_registered_secrets()


def test_without_a_token_the_call_carries_no_authorization():
    opener = StandInOpener(REPOSITORY_JSON)

    details = fetch_github_repository_details(REFERENCE, open_url=opener)

    assert opener.authorization_headers() == [None]
    assert details.license_spdx_id == "BSD-3-Clause"
    assert details.size_bytes == 2048 * 1024


def test_a_token_is_sent_as_a_bearer_token():
    opener = StandInOpener(REPOSITORY_JSON)

    fetch_github_repository_details(REFERENCE, api_token=FINE_GRAINED_TOKEN, open_url=opener)

    assert opener.authorization_headers() == [f"Bearer {FINE_GRAINED_TOKEN}"]


@pytest.mark.parametrize(
    "error",
    [
        http_error(403, "rate limit exceeded"),
        http_error(403, "Forbidden", {"x-ratelimit-remaining": "0"}),
        http_error(403, "You have exceeded a secondary rate limit"),
        http_error(429, "Too Many Requests", {"retry-after": "60"}),
    ],
)
def test_a_rate_limit_refusal_is_its_own_error_with_a_fixed_message(error):
    opener = StandInOpener(error)

    with pytest.raises(GitHubRateLimitedError) as raised:
        fetch_github_repository_details(REFERENCE, open_url=opener)

    assert str(raised.value) == GITHUB_RATE_LIMITED_MESSAGE


def test_another_refusal_is_not_mistaken_for_the_rate_limit():
    opener = StandInOpener(http_error(403, "Forbidden", {"x-ratelimit-remaining": "57"}))

    with pytest.raises(RepositoryError) as raised:
        fetch_github_repository_details(REFERENCE, open_url=opener)

    assert not isinstance(raised.value, GitHubRateLimitedError)
    assert "HTTP 403" in str(raised.value)


def test_a_missing_repository_is_still_not_found():
    opener = StandInOpener(http_error(404, "Not Found"))

    with pytest.raises(RepositoryNotFoundError):
        fetch_github_repository_details(REFERENCE, open_url=opener)


def test_a_refused_token_is_dropped_and_the_call_made_again_without_it(caplog):
    opener = StandInOpener(http_error(401, "Bad credentials"), REPOSITORY_JSON)

    with caplog.at_level(logging.WARNING):
        details = fetch_github_repository_details(
            REFERENCE, api_token=FINE_GRAINED_TOKEN, open_url=opener
        )

    assert details.license_spdx_id == "BSD-3-Clause"
    assert opener.authorization_headers() == [f"Bearer {FINE_GRAINED_TOKEN}", None]
    assert "GITHUB_API_TOKEN" in caplog.text
    assert FINE_GRAINED_TOKEN not in caplog.text


def test_a_401_without_a_token_is_an_ordinary_failure():
    opener = StandInOpener(http_error(401, "Requires authentication"))

    with pytest.raises(RepositoryError) as raised:
        fetch_github_repository_details(REFERENCE, open_url=opener)

    assert "HTTP 401" in str(raised.value)
    assert len(opener.requests) == 1


@pytest.mark.parametrize("value", [None, ""])
def test_no_token_is_read_when_the_setting_is_unset_or_empty(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("GITHUB_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("GITHUB_API_TOKEN", value)

    assert read_github_api_token() is None


def test_a_fine_grained_token_is_read_and_registered_for_redaction(monkeypatch):
    monkeypatch.setenv("GITHUB_API_TOKEN", FINE_GRAINED_TOKEN)

    assert read_github_api_token() == FINE_GRAINED_TOKEN
    assert FINE_GRAINED_TOKEN not in redact(f"token {FINE_GRAINED_TOKEN} leaked")


def test_a_classic_token_is_refused_without_showing_it(monkeypatch):
    monkeypatch.setenv("GITHUB_API_TOKEN", CLASSIC_TOKEN)

    with pytest.raises(InvalidGitHubTokenError) as raised:
        read_github_api_token()

    assert CLASSIC_TOKEN not in str(raised.value)
    assert "github_pat_" in str(raised.value)


def test_start_up_stops_on_a_classic_token_before_touching_the_database(monkeypatch):
    monkeypatch.setenv("GITHUB_API_TOKEN", CLASSIC_TOKEN)

    with pytest.raises(InvalidGitHubTokenError):
        build_indexing_job_runner(database=None, gemini_client=None)


def test_a_clone_asks_github_with_the_token_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_API_TOKEN", FINE_GRAINED_TOKEN)
    tokens_used = []

    def recording_fetch(reference, api_token=None):
        tokens_used.append(api_token)
        raise RepositoryNotFoundError("stop before cloning")

    monkeypatch.setattr(repository_cloner, "fetch_github_repository_details", recording_fetch)

    with pytest.raises(RepositoryNotFoundError):
        clone_repository(REFERENCE, "3.1.3", repositories_directory=tmp_path)

    assert tokens_used == [FINE_GRAINED_TOKEN]


@pytest.mark.parametrize(
    "new_url, keeps_token",
    [
        ("https://api.github.com/repositories/596892", True),
        ("https://example.com/repositories/596892", False),
        ("http://api.github.com/repositories/596892", False),
    ],
)
def test_a_redirect_keeps_the_token_only_on_githubs_api_over_https(new_url, keeps_token):
    request = urllib.request.Request(
        "https://api.github.com/repos/pallets/flask",
        headers={"Authorization": f"Bearer {FINE_GRAINED_TOKEN}"},
    )

    redirected = TokenDroppingRedirectHandler().redirect_request(
        request, None, 301, "Moved Permanently", {}, new_url
    )

    assert redirected.has_header("Authorization") is keeps_token
