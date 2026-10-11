from __future__ import annotations

from datetime import datetime

from retrieval.fingerprint_store import (
    MongoFingerprintStore,
    fingerprints_document,
    stored_fingerprints_from,
)
from retrieval.fingerprints import PathFingerprint, build_version_fingerprints, fingerprint_bytes
from retrieval.repository_cloner import CloneMetadata

METADATA = CloneMetadata(
    repository="owner/project",
    version="main",
    commit_id="a" * 40,
    license_spdx_id="MIT",
    license_name="MIT License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-10-11T00:00:00+00:00",
    license_path="LICENSE",
)

FINGERPRINTS = build_version_fingerprints(
    [
        PathFingerprint("src/project/app.py", fingerprint_bytes(b"one\n")),
        PathFingerprint("setup.py", fingerprint_bytes(b"two\n")),
    ]
)


class FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def replace_one(self, query: dict, document: dict, upsert: bool) -> None:
        assert upsert is True
        self.documents[query["_id"]] = document

    def find_one(self, query: dict) -> dict | None:
        for document in self.documents.values():
            if all(document[field] == value for field, value in query.items()):
                return document
        return None


def test_the_document_shares_the_version_record_id_and_lists_paths_without_dotted_keys():
    document = fingerprints_document(METADATA, FINGERPRINTS, format_version=1)

    assert document["_id"] == "owner/project@main"
    assert document["commit_id"] == "a" * 40
    assert document["format_version"] == 1
    assert document["root"] == FINGERPRINTS.root
    assert document["files"] == [
        {"path": "setup.py", "fingerprint": fingerprint_bytes(b"two\n")},
        {"path": "src/project/app.py", "fingerprint": fingerprint_bytes(b"one\n")},
    ]
    assert [folder["path"] for folder in document["folders"]] == ["", "src", "src/project"]
    assert isinstance(document["indexed_at"], datetime)


def test_a_saved_document_reads_back_as_the_same_fingerprints():
    document = fingerprints_document(METADATA, FINGERPRINTS, format_version=1)

    stored = stored_fingerprints_from(document)

    assert stored.repository == "owner/project"
    assert stored.version == "main"
    assert stored.commit_id == "a" * 40
    assert stored.format_version == 1
    assert stored.fingerprints == FINGERPRINTS


def test_saving_a_version_again_replaces_its_fingerprints():
    collection = FakeCollection()
    store = MongoFingerprintStore({"version_fingerprints": collection})
    newer = build_version_fingerprints([PathFingerprint("setup.py", fingerprint_bytes(b"new\n"))])

    store.save(METADATA, FINGERPRINTS, format_version=1)
    store.save(METADATA, newer, format_version=2)

    assert len(collection.documents) == 1
    stored = store.find("owner/project", "main")
    assert stored.fingerprints == newer
    assert stored.format_version == 2


def test_a_version_without_fingerprints_is_none():
    store = MongoFingerprintStore({"version_fingerprints": FakeCollection()})

    assert store.find("owner/project", "main") is None
