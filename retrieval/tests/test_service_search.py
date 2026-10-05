from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from retrieval.asking import EmbeddingUse, SearchRun
from retrieval.embedders import EmbeddingRequestError
from retrieval.embedding_budget import EmbeddingBudgetExhaustedError
from retrieval.redaction import clear_registered_secrets
from retrieval.search_results import SearchRefusedError, SearchResult
from retrieval.service.app import create_app

SERVICE_TOKEN = "test-service-token-0123456789abcdef"

AUTHORIZED = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

COMMIT_ID = "22d924701a6ae2e4cd01e9a15bbaf3946094af65"

REPOSITORY_RECORD = {
    "commit_id": COMMIT_ID,
    "license_spdx_id": "BSD-3-Clause",
    "license_name": 'BSD 3-Clause "New" or "Revised" License',
    "license_path": "LICENSE.txt",
}

VALID_BODY = {
    "repository": "pallets/flask",
    "version": "3.1.3",
    "query": "  how is the session cookie signed  ",
}

SIGNING_RESULT = SearchResult(
    chunk_id="signing",
    file_path="src/flask/sessions.py",
    start_line=303,
    end_line=320,
    kind="method",
    qualified_name="SecureCookieSessionInterface.get_signing_serializer",
    signature=None,
    part_number=1,
    part_count=1,
    is_test_file=False,
    text="\n".join(f"line {number}" for number in range(303, 321)),
    score=0.83,
)

TEST_RESULT = SearchResult(
    chunk_id="test",
    file_path="tests/test_basic.py",
    start_line=40,
    end_line=42,
    kind="function",
    qualified_name="test_session",
    signature=None,
    part_number=1,
    part_count=1,
    is_test_file=True,
    text="def test_session():\n    pass\n",
    score=0.71,
)

OUTLINE_RESULT = SearchResult(
    chunk_id="outline",
    file_path="src/flask/sessions.py",
    start_line=284,
    end_line=385,
    kind="class",
    qualified_name="SecureCookieSessionInterface",
    signature=None,
    part_number=1,
    part_count=1,
    is_test_file=False,
    text="class SecureCookieSessionInterface(SessionInterface):\n    def open_session(...): ...",
    score=0.7,
)

SEARCH_RUN = SearchRun(
    results=[SIGNING_RESULT, TEST_RESULT, OUTLINE_RESULT],
    timings={"embed query": 31.4, "vector search": 47.6},
    embedding_use=EmbeddingUse.FROM_CACHE,
    repository_record=REPOSITORY_RECORD,
)


class FakeAsker:
    def __init__(self, outcome=SEARCH_RUN, delay_seconds: float = 0.0) -> None:
        self.outcome = outcome
        self.delay_seconds = delay_seconds
        self.calls: list[dict] = []
        self.active = 0
        self.most_active = 0
        self._counter_lock = threading.Lock()

    def search(self, query, repository, version, limit=10, exclude_tests=False):
        self.calls.append(
            {
                "method": "search",
                "query": query,
                "repository": repository,
                "version": version,
                "limit": limit,
                "exclude_tests": exclude_tests,
            }
        )
        self._wait()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def ask(self, question, repository, version, exclude_tests=False):
        self.calls.append({"method": "ask"})
        self._wait()
        raise SearchRefusedError("not needed for this test")

    def _wait(self) -> None:
        with self._counter_lock:
            self.active += 1
            self.most_active = max(self.most_active, self.active)
        time.sleep(self.delay_seconds)
        with self._counter_lock:
            self.active -= 1


@pytest.fixture(autouse=True)
def _forget_registered_secrets():
    yield
    clear_registered_secrets()


def _client(asker: FakeAsker, now: datetime | None = None):
    clock = now or datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    return create_app(service_token=SERVICE_TOKEN, asker=asker, now=lambda: clock).test_client()


def test_a_query_returns_ranked_results_with_snippets_links_and_license():
    asker = FakeAsker()

    response = _client(asker).post("/search", json=VALID_BODY, headers=AUTHORIZED)
    reply = response.get_json()

    assert response.status_code == 200
    assert asker.calls == [
        {
            "method": "search",
            "query": "how is the session cookie signed",
            "repository": "pallets/flask",
            "version": "3.1.3",
            "limit": 10,
            "exclude_tests": False,
        }
    ]
    first = reply["results"][0]
    assert {key: value for key, value in first.items() if key != "snippet"} == {
        "rank": 1,
        "file_path": "src/flask/sessions.py",
        "kind": "method",
        "qualified_name": "SecureCookieSessionInterface.get_signing_serializer",
        "start_line": 303,
        "end_line": 320,
        "is_test_file": False,
        "score": 0.83,
        "link": f"https://github.com/pallets/flask/blob/{COMMIT_ID}/src/flask/sessions.py#L303-L320",
    }
    assert [result["rank"] for result in reply["results"]] == [1, 2, 3]
    assert reply["results"][1]["is_test_file"] is True
    assert reply["repository"] == {
        "name": "pallets/flask",
        "version": "3.1.3",
        "commit_id": COMMIT_ID,
        "license": {
            "spdx_id": "BSD-3-Clause",
            "name": 'BSD 3-Clause "New" or "Revised" License',
            "path": "LICENSE.txt",
            "link": f"https://github.com/pallets/flask/blob/{COMMIT_ID}/LICENSE.txt",
        },
    }
    assert reply["usage"] == {"embedding": "from the cache"}
    assert reply["timings_ms"] == {"embed query": 31, "vector search": 48}


def test_snippets_show_the_opening_lines_and_never_more_than_twelve():
    reply = _client(FakeAsker()).post("/search", json=VALID_BODY, headers=AUTHORIZED).get_json()
    long, short, outline = (result["snippet"] for result in reply["results"])

    assert len(long["lines"]) == 12
    assert long["lines"][0] == {"number": 303, "text": "line 303"}
    assert long["lines"][-1]["number"] == 314
    assert long["more_lines"] == 6
    assert long["is_outline"] is False
    assert [line["number"] for line in short["lines"]] == [40, 41, 42]
    assert short["more_lines"] == 0
    assert outline["is_outline"] is True
    assert outline["lines"][0] == {
        "number": None,
        "text": "class SecureCookieSessionInterface(SessionInterface):",
    }
    assert "text" not in reply["results"][0]


def test_limit_and_exclude_tests_are_passed_through():
    asker = FakeAsker()

    _client(asker).post(
        "/search", json={**VALID_BODY, "limit": 20, "exclude_tests": True}, headers=AUTHORIZED
    )

    assert asker.calls[0]["limit"] == 20
    assert asker.calls[0]["exclude_tests"] is True


def test_no_matching_code_is_an_empty_list():
    no_results = SearchRun(
        results=[],
        timings={"embed query": 600.0, "vector search": 40.0},
        embedding_use=EmbeddingUse.EMBEDDED,
        repository_record=REPOSITORY_RECORD,
    )

    response = _client(FakeAsker(no_results)).post("/search", json=VALID_BODY, headers=AUTHORIZED)
    reply = response.get_json()

    assert response.status_code == 200
    assert reply["results"] == []
    assert reply["usage"] == {"embedding": "embedded and cached"}
    assert reply["repository"]["license"]["link"].endswith(f"/blob/{COMMIT_ID}/LICENSE.txt")


@pytest.mark.parametrize(
    "body, message",
    [
        (["not", "an", "object"], "JSON object"),
        ({**VALID_BODY, "question": "hello"}, "Unknown fields: question"),
        ({**VALID_BODY, "mode": "keyword"}, "Unknown fields: mode"),
        ({"repository": "pallets/flask", "version": "3.1.3"}, "Missing fields: query"),
        ({**VALID_BODY, "repository": "pallets"}, "repository must be owner/repo"),
        ({**VALID_BODY, "version": "--upload-pack=x"}, "version must be"),
        ({**VALID_BODY, "query": "   "}, "query must be a non-blank string"),
        ({**VALID_BODY, "query": ["list"]}, "query must be a non-blank string"),
        ({**VALID_BODY, "query": "x" * 1001}, "query must be at most 1000 characters"),
        ({**VALID_BODY, "exclude_tests": "no"}, "exclude_tests must be true or false"),
        ({**VALID_BODY, "limit": 0}, "limit must be a whole number from 1 to 20"),
        ({**VALID_BODY, "limit": 21}, "limit must be a whole number from 1 to 20"),
        ({**VALID_BODY, "limit": 5.0}, "limit must be a whole number from 1 to 20"),
        ({**VALID_BODY, "limit": "5"}, "limit must be a whole number from 1 to 20"),
        ({**VALID_BODY, "limit": True}, "limit must be a whole number from 1 to 20"),
    ],
)
def test_a_body_that_breaks_a_rule_is_refused_before_any_search(body, message):
    asker = FakeAsker()

    response = _client(asker).post("/search", json=body, headers=AUTHORIZED)

    assert response.status_code == 400
    assert message in response.get_json()["error"]
    assert asker.calls == []


@pytest.mark.parametrize("limit", [1, 20])
def test_the_limit_bounds_are_accepted(limit):
    asker = FakeAsker()

    response = _client(asker).post(
        "/search", json={**VALID_BODY, "limit": limit}, headers=AUTHORIZED
    )

    assert response.status_code == 200
    assert asker.calls[0]["limit"] == limit


def test_searching_needs_the_token():
    asker = FakeAsker()

    response = _client(asker).post("/search", json=VALID_BODY)

    assert response.status_code == 401
    assert asker.calls == []


def test_searching_needs_post():
    response = _client(FakeAsker()).get("/search", headers=AUTHORIZED)

    assert response.status_code == 405
    assert response.is_json


def test_a_version_that_cannot_be_searched_is_a_conflict():
    refusal = SearchRefusedError("pallets/flask at 9.9.9 has not finished indexing")

    response = _client(FakeAsker(refusal)).post("/search", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 409
    assert response.get_json() == {"error": "pallets/flask at 9.9.9 has not finished indexing"}


def test_a_used_up_embedding_quota_says_to_wait_until_midnight_pacific():
    quota_error = EmbeddingRequestError(
        "429 RESOURCE_EXHAUSTED",
        status_code=429,
        retry_after_seconds=None,
        exceeded_quota_ids=("EmbedContentRequestsPerDayPerProjectPerModel-FreeTier",),
    )
    eleven_pm_pacific = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)

    response = _client(FakeAsker(quota_error), now=eleven_pm_pacific).post(
        "/search", json=VALID_BODY, headers=AUTHORIZED
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "3600"
    assert response.get_json() == {
        "error": "Gemini's daily quota is used up; try again after it resets"
    }


@pytest.mark.parametrize(
    "failure",
    [
        EmbeddingRequestError("503 UNAVAILABLE", status_code=503, retry_after_seconds=None),
        ServerSelectionTimeoutError("mongodb+srv://user:password1234@cluster timed out"),
    ],
)
def test_a_passing_failure_says_to_retry_shortly_and_hides_details(failure):
    response = _client(FakeAsker(failure)).post("/search", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert "password1234" not in response.get_data(as_text=True)


def test_searches_and_questions_share_one_lock():
    asker = FakeAsker(delay_seconds=0.05)
    app = create_app(service_token=SERVICE_TOKEN, asker=asker)

    def search_once():
        app.test_client().post("/search", json=VALID_BODY, headers=AUTHORIZED)

    def ask_once():
        ask_body = {"repository": "pallets/flask", "version": "3.1.3", "question": "hello"}
        app.test_client().post("/ask", json=ask_body, headers=AUTHORIZED)

    threads = [threading.Thread(target=target) for target in (search_once, ask_once) * 2]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(call["method"] for call in asker.calls) == ["ask", "ask", "search", "search"]
    assert asker.most_active == 1


def test_a_used_up_embedding_budget_says_to_wait_until_midnight_pacific():
    eleven_pm_pacific = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
    budget_error = EmbeddingBudgetExhaustedError("The daily embedding budget of 1000 is used up")

    response = _client(FakeAsker(budget_error), now=eleven_pm_pacific).post(
        "/search", json=VALID_BODY, headers=AUTHORIZED
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "3600"
    assert "daily quota" in response.get_json()["error"]
