from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from retrieval.redaction import clear_registered_secrets
from retrieval.repository_overview import FileLink, FileOverview, OverviewRun, RepositoryOverview
from retrieval.search_results import SearchRefusedError
from retrieval.service.app import create_app

SERVICE_TOKEN = "test-service-token-0123456789abcdef"

AUTHORIZED = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

VALID_BODY = {"repository": "pallets/flask", "version": "3.1.3"}

COMMIT = "22d924701a6ae2e4cd01e9a15bbaf3946094af65"

RECORD = {
    "commit_id": COMMIT,
    "license_spdx_id": "BSD-3-Clause",
    "license_name": "BSD 3-Clause License",
    "license_path": "LICENSE.txt",
}

OVERVIEW = RepositoryOverview(
    files=[
        FileOverview("src/flask/app.py", 1, 40, calls_within=12, calls_out=9, calls_in=2),
        FileOverview("src/flask/helpers.py", 0, 20, calls_within=3, calls_out=2, calls_in=9),
    ],
    links=[
        FileLink("src/flask/app.py", "src/flask/helpers.py", 9),
        FileLink("src/flask/helpers.py", "src/flask/app.py", 2),
    ],
    left_out_folders=("examples",),
    left_out_file_count=10,
    left_out_call_count=41,
)


@pytest.fixture(autouse=True)
def _forget_registered_secrets():
    yield
    clear_registered_secrets()


class UnusedAsker:
    def __getattr__(self, name):
        raise AssertionError("overview requests never use the question asker")


class UnusedRunner:
    def start(self, repository, version):
        raise AssertionError("overview requests never index")

    def find_job(self, job_id):
        raise AssertionError("overview requests never index")


class FakeBuilder:
    def __init__(self, failure: Exception | None = None):
        self.failure = failure
        self.calls = []

    def overview(self, repository, version):
        self.calls.append((repository, version))
        if self.failure is not None:
            raise self.failure
        return OverviewRun(
            overview=OVERVIEW,
            repository_record=RECORD,
            timings={"checks": 31.4, "summarize call graph": 18.6},
        )


def _client(builder):
    return create_app(
        service_token=SERVICE_TOKEN,
        asker=UnusedAsker(),
        indexing_runner=UnusedRunner(),
        now=lambda: datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
        overview_builder=builder,
    ).test_client()


def test_an_overview_lists_files_links_totals_and_the_license():
    builder = FakeBuilder()

    response = _client(builder).post("/overview", json=VALID_BODY, headers=AUTHORIZED)
    reply = response.get_json()

    assert response.status_code == 200
    assert builder.calls == [("pallets/flask", "3.1.3")]
    assert reply["files"][0] == {
        "path": "src/flask/app.py",
        "classes": 1,
        "functions": 40,
        "calls_within": 12,
        "calls_out": 9,
        "calls_in": 2,
        "link": f"https://github.com/pallets/flask/blob/{COMMIT}/src/flask/app.py",
    }
    assert reply["links"] == [
        {"from": "src/flask/app.py", "to": "src/flask/helpers.py", "calls": 9},
        {"from": "src/flask/helpers.py", "to": "src/flask/app.py", "calls": 2},
    ]
    assert reply["totals"] == {
        "files": 2,
        "classes": 1,
        "functions": 60,
        "calls": 26,
        "calls_between_files": 11,
        "calls_within_files": 15,
    }
    assert reply["repository"]["license"]["link"] == (
        f"https://github.com/pallets/flask/blob/{COMMIT}/LICENSE.txt"
    )
    assert reply["left_out"] == {"folders": ["examples/"], "files": 10, "calls": 41}
    assert reply["timings_ms"] == {"checks": 31, "summarize call graph": 19}


def test_the_overview_needs_the_service_token():
    response = _client(FakeBuilder()).post("/overview", json=VALID_BODY)

    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"repository": "pallets/flask"},
        {**VALID_BODY, "limit": 3},
        {"repository": "not a repository", "version": "3.1.3"},
        "pallets/flask",
    ],
)
def test_a_bad_body_is_refused_before_building(body):
    builder = FakeBuilder()

    response = _client(builder).post("/overview", json=body, headers=AUTHORIZED)

    assert response.status_code == 400
    assert builder.calls == []


def test_a_version_that_cannot_be_described_is_a_conflict():
    builder = FakeBuilder(SearchRefusedError("pallets/flask at 9.9.9 has not finished indexing"))

    response = _client(builder).post(
        "/overview", json={"repository": "pallets/flask", "version": "9.9.9"}, headers=AUTHORIZED
    )

    assert response.status_code == 409


def test_a_database_failure_asks_the_caller_to_retry():
    builder = FakeBuilder(ServerSelectionTimeoutError("cluster0.example.mongodb.net timed out"))

    response = _client(builder).post("/overview", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert "mongodb.net" not in response.get_data(as_text=True)


def test_without_a_builder_the_route_does_not_exist():
    client = create_app(
        service_token=SERVICE_TOKEN, asker=UnusedAsker(), indexing_runner=UnusedRunner()
    ).test_client()

    response = client.post("/overview", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 404
