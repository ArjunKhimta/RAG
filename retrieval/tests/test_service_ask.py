from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from retrieval.answer_generation import (
    AnswerRejectedError,
    AnswerSentence,
    Citation,
    GeneratedAnswer,
    ModelReply,
)
from retrieval.asking import AskRun, EmbeddingUse, NoSourcesFoundError
from retrieval.gemini_errors import GeminiRequestError
from retrieval.query_router import vector_without_router
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
}

VALID_BODY = {
    "repository": "pallets/flask",
    "version": "3.1.3",
    "question": "  How does Flask sign the session cookie?  ",
}

SIGNING_SOURCE = SearchResult(
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
    score=0.9,
)

OUTLINE_SOURCE = SearchResult(
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
    score=0.8,
)

SENTENCES = [
    AnswerSentence("It checks the secret key.", [Citation(1, 303, 305)]),
    AnswerSentence("It builds a serializer.", [Citation(1, 303, 320), Citation(2, 290, 295)]),
    AnswerSentence("It checks the secret key again.", [Citation(1, 303, 305)]),
]

GENERATED = GeneratedAnswer(
    found_answer=True,
    answer="It checks the secret key [1]. It builds a serializer [1, 2]. ...",
    citations=[citation for sentence in SENTENCES for citation in sentence.citations],
    sources=[SIGNING_SOURCE, OUTLINE_SOURCE],
    reply=ModelReply(text="{}", prompt_tokens=2213, output_tokens=114, attempt_count=1),
    sentences=SENTENCES,
)

ASK_RUN = AskRun(
    generated=GENERATED,
    timings={"embed query": 36.4, "vector search": 45.2, "generate answer": 2043.0},
    embedding_use=EmbeddingUse.FROM_CACHE,
    expansion=None,
    route=vector_without_router("question"),
    repository_record=REPOSITORY_RECORD,
)


class FakeAsker:
    def __init__(self, outcome=ASK_RUN, delay_seconds: float = 0.0) -> None:
        self.outcome = outcome
        self.delay_seconds = delay_seconds
        self.calls: list[dict] = []
        self.active = 0
        self.most_active = 0
        self._counter_lock = threading.Lock()

    def ask(self, question, repository, version, exclude_tests=False):
        with self._counter_lock:
            self.active += 1
            self.most_active = max(self.most_active, self.active)
        self.calls.append(
            {
                "question": question,
                "repository": repository,
                "version": version,
                "exclude_tests": exclude_tests,
            }
        )
        time.sleep(self.delay_seconds)
        with self._counter_lock:
            self.active -= 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture(autouse=True)
def _forget_registered_secrets():
    yield
    clear_registered_secrets()


def _client(asker: FakeAsker, now: datetime | None = None):
    clock = now or datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    return create_app(service_token=SERVICE_TOKEN, asker=asker, now=lambda: clock).test_client()


def test_a_question_is_answered_with_sentences_sources_snippets_and_license():
    asker = FakeAsker()

    response = _client(asker).post("/ask", json=VALID_BODY, headers=AUTHORIZED)
    reply = response.get_json()

    assert response.status_code == 200
    assert asker.calls == [
        {
            "question": "How does Flask sign the session cookie?",
            "repository": "pallets/flask",
            "version": "3.1.3",
            "exclude_tests": False,
        }
    ]
    assert reply["found_answer"] is True
    assert reply["sentences"][1] == {
        "text": "It builds a serializer.",
        "citations": [
            {"source": 1, "start_line": 303, "end_line": 320},
            {"source": 2, "start_line": 290, "end_line": 295},
        ],
    }
    assert reply["sources"][0] == {
        "number": 1,
        "file_path": "src/flask/sessions.py",
        "kind": "method",
        "qualified_name": "SecureCookieSessionInterface.get_signing_serializer",
        "start_line": 303,
        "end_line": 320,
        "link": f"https://github.com/pallets/flask/blob/{COMMIT_ID}/src/flask/sessions.py#L303-L320",
    }
    assert "text" not in reply["sources"][0]
    assert reply["repository"] == {
        "name": "pallets/flask",
        "version": "3.1.3",
        "commit_id": COMMIT_ID,
        "license": {"spdx_id": "BSD-3-Clause", "name": 'BSD 3-Clause "New" or "Revised" License'},
    }
    assert reply["usage"] == {
        "embedding": "from the cache",
        "answer_requests": 1,
        "prompt_tokens": 2213,
        "output_tokens": 114,
    }
    assert reply["timings_ms"] == {"embed query": 36, "vector search": 45, "generate answer": 2043}


def test_snippets_cover_each_citation_once_and_never_more_than_twelve_lines():
    reply = _client(FakeAsker()).post("/ask", json=VALID_BODY, headers=AUTHORIZED).get_json()
    snippets = reply["snippets"]

    assert [(snippet["source"], snippet["start_line"]) for snippet in snippets] == [
        (1, 303),
        (1, 303),
        (2, 290),
    ]
    short, long, outline = snippets
    assert [line["number"] for line in short["lines"]] == [303, 304, 305]
    assert short["lines"][0]["text"] == "line 303"
    assert short["more_lines"] == 0
    assert len(long["lines"]) == 12
    assert long["more_lines"] == 6
    assert long["link"].endswith("#L303-L320")
    assert outline["is_outline"] is True
    assert outline["lines"][0] == {
        "number": None,
        "text": "class SecureCookieSessionInterface(SessionInterface):",
    }


def test_exclude_tests_is_passed_through():
    asker = FakeAsker()

    _client(asker).post("/ask", json={**VALID_BODY, "exclude_tests": True}, headers=AUTHORIZED)

    assert asker.calls[0]["exclude_tests"] is True


def test_no_matching_code_is_a_normal_reply():
    asker = FakeAsker(NoSourcesFoundError(REPOSITORY_RECORD))

    response = _client(asker).post("/ask", json=VALID_BODY, headers=AUTHORIZED)
    reply = response.get_json()

    assert response.status_code == 200
    assert reply["found_answer"] is False
    assert reply["sentences"] == reply["sources"] == reply["snippets"] == []
    assert reply["repository"]["license"]["spdx_id"] == "BSD-3-Clause"


@pytest.mark.parametrize(
    "body, message",
    [
        (["not", "an", "object"], "JSON object"),
        ({**VALID_BODY, "exclude_test": True}, "Unknown fields: exclude_test"),
        ({"repository": "pallets/flask", "version": "3.1.3"}, "Missing fields: question"),
        ({**VALID_BODY, "repository": "pallets"}, "repository must be owner/repo"),
        ({**VALID_BODY, "repository": "pallets/flask/extra"}, "repository must be owner/repo"),
        ({**VALID_BODY, "repository": "pallets/.."}, "repository must be owner/repo"),
        ({**VALID_BODY, "repository": "-bad/flask"}, "repository must be owner/repo"),
        ({**VALID_BODY, "version": "--upload-pack=x"}, "version must be"),
        ({**VALID_BODY, "version": "release/3.1"}, "version must be"),
        ({**VALID_BODY, "question": "   "}, "question must be a non-blank string"),
        ({**VALID_BODY, "question": 42}, "question must be a non-blank string"),
        ({**VALID_BODY, "question": "x" * 1001}, "at most 1000 characters"),
        ({**VALID_BODY, "exclude_tests": "yes"}, "exclude_tests must be true or false"),
        ({**VALID_BODY, "exclude_tests": 1}, "exclude_tests must be true or false"),
    ],
)
def test_a_body_that_breaks_a_rule_is_refused_before_any_search(body, message):
    asker = FakeAsker()

    response = _client(asker).post("/ask", json=body, headers=AUTHORIZED)

    assert response.status_code == 400
    assert message in response.get_json()["error"]
    assert asker.calls == []


def test_a_question_of_exactly_the_limit_is_accepted():
    response = _client(FakeAsker()).post(
        "/ask", json={**VALID_BODY, "question": "x" * 1000}, headers=AUTHORIZED
    )

    assert response.status_code == 200


def test_a_body_that_is_not_json_is_refused():
    response = _client(FakeAsker()).post(
        "/ask", data="question=hello", headers={**AUTHORIZED, "Content-Type": "text/plain"}
    )

    assert response.status_code == 400
    assert "JSON object" in response.get_json()["error"]


def test_a_body_over_sixteen_kilobytes_is_refused():
    huge_body = {**VALID_BODY, "question": "x" * (17 * 1024)}

    response = _client(FakeAsker()).post("/ask", json=huge_body, headers=AUTHORIZED)

    assert response.status_code == 413
    assert response.is_json


def test_asking_needs_the_token():
    asker = FakeAsker()

    response = _client(asker).post("/ask", json=VALID_BODY)

    assert response.status_code == 401
    assert asker.calls == []


def test_a_version_that_cannot_be_searched_is_a_conflict():
    refusal = SearchRefusedError("pallets/flask at 9.9.9 has not finished indexing")

    response = _client(FakeAsker(refusal)).post("/ask", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 409
    assert response.get_json() == {"error": "pallets/flask at 9.9.9 has not finished indexing"}


def test_a_rejected_answer_is_never_shown():
    rejection = AnswerRejectedError(
        ["sentence 1 writes its own marker [2]"], answer="The secret answer text [2]."
    )

    response = _client(FakeAsker(rejection)).post("/ask", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 502
    assert "secret answer text" not in response.get_data(as_text=True)


def test_a_used_up_daily_quota_says_to_wait_until_midnight_pacific():
    quota_error = GeminiRequestError(
        "429 RESOURCE_EXHAUSTED",
        status_code=429,
        retry_after_seconds=None,
        exceeded_quota_ids=("GenerateRequestsPerDayPerProjectPerModel-FreeTier",),
    )
    eleven_pm_pacific = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)

    response = _client(FakeAsker(quota_error), now=eleven_pm_pacific).post(
        "/ask", json=VALID_BODY, headers=AUTHORIZED
    )

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "3600"
    assert "daily quota" in response.get_json()["error"]


@pytest.mark.parametrize(
    "failure",
    [
        GeminiRequestError("503 UNAVAILABLE", status_code=503, retry_after_seconds=None),
        ServerSelectionTimeoutError("mongodb+srv://user:password1234@cluster timed out"),
    ],
)
def test_a_passing_failure_says_to_retry_shortly_and_hides_details(failure):
    response = _client(FakeAsker(failure)).post("/ask", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert "password1234" not in response.get_data(as_text=True)


def test_questions_are_answered_one_at_a_time():
    asker = FakeAsker(delay_seconds=0.05)
    app = create_app(service_token=SERVICE_TOKEN, asker=asker)

    def ask_once():
        app.test_client().post("/ask", json=VALID_BODY, headers=AUTHORIZED)

    threads = [threading.Thread(target=ask_once) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(asker.calls) == 4
    assert asker.most_active == 1
