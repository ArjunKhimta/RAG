"""Save and read version fingerprints in real MongoDB Atlas. Excluded from the default run.

    pytest retrieval/tests -m integration

Writes only to the `code_search_test` database, which is dropped afterwards. No Gemini requests.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace

import pytest

from atlas_test_databases import empty_database
from retrieval.clients import build_mongo_client
from retrieval.config import MONGODB_URI_VARIABLE, load_environment
from retrieval.fingerprint_store import FINGERPRINTS_COLLECTION, MongoFingerprintStore
from retrieval.fingerprints import PathFingerprint, build_version_fingerprints, fingerprint_bytes
from retrieval.repository_cloner import CloneMetadata

pytestmark = pytest.mark.integration

TEST_DATABASE = "code_search_test"

METADATA = CloneMetadata(
    repository="integration/test",
    version="main",
    commit_id="b" * 40,
    license_spdx_id="MIT",
    license_name="MIT License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-10-11T00:00:00+00:00",
    license_path="LICENSE",
)


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    if not os.environ.get(MONGODB_URI_VARIABLE):
        pytest.skip(f"{MONGODB_URI_VARIABLE} is not configured")


@pytest.fixture
def test_database() -> Iterator:
    mongo_client = build_mongo_client()
    empty_database(mongo_client, TEST_DATABASE)
    yield mongo_client[TEST_DATABASE]
    empty_database(mongo_client, TEST_DATABASE)
    mongo_client.close()


def test_fingerprints_with_dotted_and_non_ascii_paths_read_back_unchanged(test_database):
    store = MongoFingerprintStore(test_database)
    fingerprints = build_version_fingerprints(
        [
            PathFingerprint("src/package/app.py", fingerprint_bytes(b"one\n")),
            PathFingerprint("src/package/übersetzung.py", fingerprint_bytes(b"two\n")),
            PathFingerprint("setup.py", fingerprint_bytes(b"three\n")),
        ]
    )

    store.save(METADATA, fingerprints, format_version=1)
    stored = store.find("integration/test", "main")

    assert stored.fingerprints == fingerprints
    assert stored.commit_id == "b" * 40
    assert stored.format_version == 1


def test_saving_a_newer_commit_replaces_the_one_document(test_database):
    store = MongoFingerprintStore(test_database)
    older = build_version_fingerprints([PathFingerprint("a.py", fingerprint_bytes(b"old\n"))])
    newer = build_version_fingerprints([PathFingerprint("a.py", fingerprint_bytes(b"new\n"))])

    store.save(METADATA, older, format_version=1)
    store.save(replace(METADATA, commit_id="c" * 40), newer, format_version=1)

    assert test_database[FINGERPRINTS_COLLECTION].count_documents({}) == 1
    stored = store.find("integration/test", "main")
    assert stored.commit_id == "c" * 40
    assert stored.fingerprints == newer
