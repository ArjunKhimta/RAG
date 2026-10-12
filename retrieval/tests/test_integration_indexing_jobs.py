"""Save, update, and find indexing jobs in real MongoDB Atlas. Excluded from the default run; makes
no Gemini requests.

    pytest retrieval/tests -m integration

Writes only to the `code_search_test` database, which is dropped afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from atlas_test_databases import empty_database
from retrieval.clients import build_mongo_client
from retrieval.config import INDEXING_JOB_TTL_SECONDS, MONGODB_URI_VARIABLE, load_environment
from retrieval.indexing_jobs import INDEXING_JOBS_COLLECTION, JobStatus, MongoJobStore

pytestmark = pytest.mark.integration

TEST_DATABASE = "code_search_test"

CREATED = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

RESTARTED = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    if not os.environ.get(MONGODB_URI_VARIABLE):
        pytest.skip(f"{MONGODB_URI_VARIABLE} is not configured")


@pytest.fixture
def database() -> Iterator:
    mongo_client = build_mongo_client()
    empty_database(mongo_client, TEST_DATABASE)
    yield mongo_client[TEST_DATABASE]
    empty_database(mongo_client, TEST_DATABASE)
    mongo_client.close()


def test_a_job_is_saved_updated_and_found(database):
    store = MongoJobStore(database)
    store.ensure_indexes()

    store.create("job-1", "pallets/itsdangerous", "2.2.0", CREATED)
    store.update("job-1", {"stage": "embedding", "embedded_count": 80}, RESTARTED)

    job = store.find("job-1")
    assert job["status"] == JobStatus.RUNNING
    assert (job["repository"], job["version"]) == ("pallets/itsdangerous", "2.2.0")
    assert (job["stage"], job["embedded_count"]) == ("embedding", 80)
    assert job["created_at"] == _as_stored(CREATED)
    assert job["updated_at"] == _as_stored(RESTARTED)
    assert store.find("no-such-job") is None


def test_only_running_jobs_are_marked_interrupted(database):
    store = MongoJobStore(database)
    store.create("running", "pallets/flask", "3.1.3", CREATED)
    store.create("done", "pallets/click", "8.1.7", CREATED)
    store.update("done", {"status": JobStatus.SUCCEEDED.value}, CREATED)

    interrupted_count = store.mark_interrupted(RESTARTED)

    assert interrupted_count == 1
    assert store.find("running")["status"] == JobStatus.INTERRUPTED
    assert store.find("running")["finished_at"] == _as_stored(RESTARTED)
    assert store.find("done")["status"] == JobStatus.SUCCEEDED


def test_jobs_expire_thirty_days_after_creation(database):
    MongoJobStore(database).ensure_indexes()

    indexes = database[INDEXING_JOBS_COLLECTION].index_information()

    assert indexes["created_at_1"]["expireAfterSeconds"] == INDEXING_JOB_TTL_SECONDS


def _as_stored(moment: datetime) -> datetime:
    """MongoDB stores UTC and the client returns it without a time zone."""
    return moment.astimezone(UTC).replace(tzinfo=None)
