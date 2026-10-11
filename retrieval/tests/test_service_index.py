from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pymongo.errors import ServerSelectionTimeoutError

from retrieval.indexing_jobs import StartKind, StartOutcome, new_job_document
from retrieval.redaction import clear_registered_secrets
from retrieval.repository_cloner import CloneFailedError, VersionNotFoundError
from retrieval.service.app import create_app

SERVICE_TOKEN = "test-service-token-0123456789abcdef"

AUTHORIZED = {"Authorization": f"Bearer {SERVICE_TOKEN}"}

JOB_ID = "0123456789abcdef0123456789abcdef"

COMMIT_ID = "22d924701a6ae2e4cd01e9a15bbaf3946094af65"

NOW = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)

CREATED = datetime(2026, 10, 6, 5, 55, 30)

VALID_BODY = {"repository": "pallets/itsdangerous", "version": "2.2.0"}

REPOSITORY_RECORD = {
    "commit_id": COMMIT_ID,
    "license_spdx_id": "BSD-3-Clause",
    "license_name": 'BSD 3-Clause "New" or "Revised" License',
    "license_path": "LICENSE.txt",
}


class UnusedAsker:
    def ask(self, *arguments, **options):
        raise AssertionError("these tests never ask a question")


class FakeRunner:
    def __init__(self, outcome: StartOutcome | Exception | None = None, jobs=None) -> None:
        self.outcome = outcome or StartOutcome(StartKind.STARTED, job_id=JOB_ID)
        self.jobs = jobs or {}
        self.starts: list[tuple[str, str]] = []
        self.refreshes: list[tuple[str, str]] = []
        self.lookups: list[str] = []

    def start(self, repository, version):
        self.starts.append((repository, version))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def refresh(self, repository, version):
        self.refreshes.append((repository, version))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def find_job(self, job_id):
        self.lookups.append(job_id)
        return self.jobs.get(job_id)


@pytest.fixture(autouse=True)
def _forget_registered_secrets():
    yield
    clear_registered_secrets()


def _client(runner: FakeRunner):
    return create_app(
        service_token=SERVICE_TOKEN, asker=UnusedAsker(), indexing_runner=runner, now=lambda: NOW
    ).test_client()


def _job(**fields) -> dict:
    job = new_job_document(JOB_ID, "pallets/itsdangerous", "2.2.0", CREATED)
    job.update(fields)
    return job


def test_a_started_job_is_accepted_with_where_to_poll():
    runner = FakeRunner()

    response = _client(runner).post("/index", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 202
    assert response.headers["Location"] == f"/index-jobs/{JOB_ID}"
    assert response.get_json() == {
        "job_id": JOB_ID,
        "status": "running",
        "status_link": f"/index-jobs/{JOB_ID}",
    }
    assert runner.starts == [("pallets/itsdangerous", "2.2.0")]


def test_the_same_version_already_running_gets_the_same_job():
    runner = FakeRunner(StartOutcome(StartKind.ALREADY_RUNNING, job_id=JOB_ID))

    response = _client(runner).post("/index", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 202
    assert response.get_json()["job_id"] == JOB_ID


def test_an_indexed_version_gets_its_repository_block_with_the_license():
    record_with_languages = {
        **REPOSITORY_RECORD,
        "other_language_files": [{"language": "C", "files": 3}],
    }
    runner = FakeRunner(
        StartOutcome(StartKind.ALREADY_INDEXED, repository_record=record_with_languages)
    )

    response = _client(runner).post("/index", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "already_indexed",
        "repository": {
            "name": "pallets/itsdangerous",
            "version": "2.2.0",
            "commit_id": COMMIT_ID,
            "license": {
                "spdx_id": "BSD-3-Clause",
                "name": 'BSD 3-Clause "New" or "Revised" License',
                "path": "LICENSE.txt",
                "link": f"https://github.com/pallets/itsdangerous/blob/{COMMIT_ID}/LICENSE.txt",
            },
            "other_language_files": [{"language": "C", "files": 3}],
        },
    }


def test_a_version_indexed_before_license_paths_is_a_conflict():
    old_record = {"commit_id": COMMIT_ID, "license_spdx_id": "BSD-3-Clause"}
    runner = FakeRunner(StartOutcome(StartKind.ALREADY_INDEXED, repository_record=old_record))

    response = _client(runner).post("/index", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 409
    assert "indexed before license files were recorded" in response.get_json()["error"]


def test_a_busy_indexer_says_to_retry_in_a_minute():
    runner = FakeRunner(StartOutcome(StartKind.BUSY))

    response = _client(runner).post("/index", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert "Another repository is being indexed" in response.get_json()["error"]


@pytest.mark.parametrize(
    "body, message",
    [
        (["not", "an", "object"], "JSON object"),
        ({**VALID_BODY, "url": "https://github.com/pallets/itsdangerous"}, "Unknown fields: url"),
        ({"repository": "pallets/itsdangerous"}, "Missing fields: version"),
        ({**VALID_BODY, "repository": "https://github.com/pallets/x"}, "repository must be"),
        ({**VALID_BODY, "version": "--upload-pack=x"}, "version must be"),
        ({**VALID_BODY, "version": "release/2.2"}, "version must be"),
    ],
)
def test_a_body_that_breaks_a_rule_is_refused_before_any_job(body, message):
    runner = FakeRunner()

    response = _client(runner).post("/index", json=body, headers=AUTHORIZED)

    assert response.status_code == 400
    assert message in response.get_json()["error"]
    assert runner.starts == []


def test_a_database_failure_says_to_retry_shortly_and_hides_details():
    failure = ServerSelectionTimeoutError("mongodb+srv://user:password1234@cluster timed out")

    response = _client(FakeRunner(failure)).post("/index", json=VALID_BODY, headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert "password1234" not in response.get_data(as_text=True)


def test_a_running_job_shows_its_stage_and_progress():
    job = _job(stage="embedding", embedded_count=80, to_embed_count=160, updated_at=NOW)
    runner = FakeRunner(jobs={JOB_ID: job})

    response = _client(runner).get(f"/index-jobs/{JOB_ID}", headers=AUTHORIZED)

    assert response.status_code == 200
    assert response.get_json() == {
        "job_id": JOB_ID,
        "repository": "pallets/itsdangerous",
        "version": "2.2.0",
        "status": "running",
        "stage": "embedding",
        "progress": {"embedded": 80, "to_embed": 160},
        "error": None,
        "resume_after": None,
        "retry_after_seconds": None,
        "result": None,
        "timings_ms": {},
        "created_at": "2026-10-06T05:55:30Z",
        "finished_at": None,
    }


def test_a_succeeded_job_shows_its_result_and_timings():
    result = {"chunk_count": 180, "cache_hit_count": 20, "commit_id": COMMIT_ID}
    job = _job(
        status="succeeded",
        result=result,
        timings_ms={"embed and store": 30101},
        finished_at=datetime(2026, 10, 6, 5, 57, 1, 250000),
    )

    reply = (
        _client(FakeRunner(jobs={JOB_ID: job}))
        .get(f"/index-jobs/{JOB_ID}", headers=AUTHORIZED)
        .get_json()
    )

    assert reply["status"] == "succeeded"
    assert reply["result"] == result
    assert reply["timings_ms"] == {"embed and store": 30101}
    assert reply["finished_at"] == "2026-10-06T05:57:01Z"


def test_a_job_waiting_for_quota_says_when_and_how_long():
    job = _job(
        status="waiting_for_quota",
        error_message="Today's indexing budget is used up",
        resume_after=datetime(2026, 10, 6, 7, 0),
    )

    reply = (
        _client(FakeRunner(jobs={JOB_ID: job}))
        .get(f"/index-jobs/{JOB_ID}", headers=AUTHORIZED)
        .get_json()
    )

    assert reply["resume_after"] == "2026-10-06T07:00:00Z"
    assert reply["retry_after_seconds"] == 3600
    assert reply["error"] == {"code": None, "message": "Today's indexing budget is used up"}


def test_a_failed_job_shows_its_code_and_message():
    job = _job(status="failed", error_code="not_public", error_message="pallets/x is private")

    reply = (
        _client(FakeRunner(jobs={JOB_ID: job}))
        .get(f"/index-jobs/{JOB_ID}", headers=AUTHORIZED)
        .get_json()
    )

    assert reply["status"] == "failed"
    assert reply["error"] == {"code": "not_public", "message": "pallets/x is private"}


def test_an_unknown_job_is_not_found():
    runner = FakeRunner()

    response = _client(runner).get(f"/index-jobs/{JOB_ID}", headers=AUTHORIZED)

    assert response.status_code == 404
    assert response.get_json() == {"error": "No such indexing job"}
    assert runner.lookups == [JOB_ID]


@pytest.mark.parametrize(
    "job_id", ["job-1", JOB_ID.upper(), JOB_ID + "0", JOB_ID[:-1], '{"$ne": null}']
)
def test_a_malformed_job_id_is_not_found_without_a_lookup(job_id):
    runner = FakeRunner()

    response = _client(runner).get(f"/index-jobs/{job_id}", headers=AUTHORIZED)

    assert response.status_code == 404
    assert runner.lookups == []


def test_both_routes_need_the_token():
    runner = FakeRunner(jobs={JOB_ID: _job()})
    client = _client(runner)

    assert client.post("/index", json=VALID_BODY).status_code == 401
    assert client.get(f"/index-jobs/{JOB_ID}").status_code == 401
    assert runner.starts == []
    assert runner.lookups == []


def test_each_route_allows_only_its_method():
    client = _client(FakeRunner())

    assert client.get("/index", headers=AUTHORIZED).status_code == 405
    assert client.post(f"/index-jobs/{JOB_ID}", headers=AUTHORIZED).status_code == 405


REFRESH_BODY = {**VALID_BODY, "refresh": True}


def test_a_refresh_still_at_the_indexed_commit_is_up_to_date_with_its_repository_block():
    runner = FakeRunner(StartOutcome(StartKind.UP_TO_DATE, repository_record=REPOSITORY_RECORD))

    response = _client(runner).post("/index", json=REFRESH_BODY, headers=AUTHORIZED)

    assert response.status_code == 200
    reply = response.get_json()
    assert reply["status"] == "up_to_date"
    assert reply["repository"]["commit_id"] == COMMIT_ID
    assert reply["repository"]["license"]["path"] == "LICENSE.txt"
    assert runner.refreshes == [("pallets/itsdangerous", "2.2.0")]
    assert runner.starts == []


def test_a_refresh_of_a_moved_version_starts_a_job():
    runner = FakeRunner()

    response = _client(runner).post("/index", json=REFRESH_BODY, headers=AUTHORIZED)

    assert response.status_code == 202
    assert response.get_json()["job_id"] == JOB_ID
    assert runner.refreshes == [("pallets/itsdangerous", "2.2.0")]


def test_without_refresh_an_indexed_version_is_never_checked_against_github():
    runner = FakeRunner()

    _client(runner).post("/index", json={**VALID_BODY, "refresh": False}, headers=AUTHORIZED)

    assert runner.starts == [("pallets/itsdangerous", "2.2.0")]
    assert runner.refreshes == []


def test_a_refresh_of_a_tag_or_branch_that_no_longer_exists_is_a_conflict():
    gone = VersionNotFoundError("2.2.0 is not a tag or branch of pallets/itsdangerous")
    runner = FakeRunner(gone)

    response = _client(runner).post("/index", json=REFRESH_BODY, headers=AUTHORIZED)

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "2.2.0 is no longer a tag or branch of pallets/itsdangerous on GitHub"
    }


def test_github_out_of_reach_is_unavailable_without_showing_git_output(caplog):
    runner = FakeRunner(CloneFailedError("git ls-remote failed: fatal: unable to access"))

    response = _client(runner).post("/index", json=REFRESH_BODY, headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.get_json() == {
        "error": "GitHub could not be reached to check for changes; try again shortly"
    }
    assert "git ls-remote failed" in caplog.text


def test_refresh_must_be_true_or_false():
    runner = FakeRunner()

    response = _client(runner).post(
        "/index", json={**VALID_BODY, "refresh": "yes"}, headers=AUTHORIZED
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "refresh must be true or false"}
    assert runner.refreshes == []
