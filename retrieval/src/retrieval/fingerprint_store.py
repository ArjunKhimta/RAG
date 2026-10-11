"""Storage for each indexed version's file fingerprints in MongoDB.

`version_fingerprints` holds one document per indexed version, with the same ID as its
`repositories` record: the commit and index format the fingerprints describe, the root
fingerprint, and every folder's and file's fingerprint as lists of path and fingerprint (lists,
because MongoDB field names cannot hold the dots in file names). It is kept apart from the
`repositories` record, which every question reads, so a large repository's file list never slows a
question down.

The document is written after the version's chunks and record, so it can be missing, or describe
an older commit if indexing stopped in between. A reader must check its `commit_id` and
`format_version` against the version's record before trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pymongo.database import Database

from retrieval.chunk_store import repository_id_for
from retrieval.fingerprints import PathFingerprint, VersionFingerprints
from retrieval.repository_cloner import CloneMetadata

FINGERPRINTS_COLLECTION = "version_fingerprints"


@dataclass(frozen=True)
class StoredFingerprints:
    repository: str
    version: str
    commit_id: str
    format_version: int
    fingerprints: VersionFingerprints


class MongoFingerprintStore:
    def __init__(self, database: Database) -> None:
        self._fingerprints = database[FINGERPRINTS_COLLECTION]

    def save(
        self, metadata: CloneMetadata, fingerprints: VersionFingerprints, format_version: int
    ) -> None:
        document = fingerprints_document(metadata, fingerprints, format_version)
        self._fingerprints.replace_one({"_id": document["_id"]}, document, upsert=True)

    def find(self, repository: str, version: str) -> StoredFingerprints | None:
        document = self._fingerprints.find_one({"repository": repository, "version": version})
        if document is None:
            return None
        return stored_fingerprints_from(document)


def fingerprints_document(
    metadata: CloneMetadata, fingerprints: VersionFingerprints, format_version: int
) -> dict[str, Any]:
    return {
        "_id": repository_id_for(metadata),
        "repository": metadata.repository,
        "version": metadata.version,
        "commit_id": metadata.commit_id,
        "format_version": format_version,
        "root": fingerprints.root,
        "folders": _path_fingerprint_documents(fingerprints.folders),
        "files": _path_fingerprint_documents(fingerprints.files),
        "indexed_at": datetime.now(UTC),
    }


def stored_fingerprints_from(document: dict[str, Any]) -> StoredFingerprints:
    return StoredFingerprints(
        repository=document["repository"],
        version=document["version"],
        commit_id=document["commit_id"],
        format_version=document["format_version"],
        fingerprints=VersionFingerprints(
            root=document["root"],
            folders=_path_fingerprints_from(document["folders"]),
            files=_path_fingerprints_from(document["files"]),
        ),
    )


def _path_fingerprint_documents(entries: tuple[PathFingerprint, ...]) -> list[dict[str, str]]:
    return [{"path": entry.path, "fingerprint": entry.fingerprint} for entry in entries]


def _path_fingerprints_from(documents: list[dict[str, str]]) -> tuple[PathFingerprint, ...]:
    return tuple(
        PathFingerprint(path=document["path"], fingerprint=document["fingerprint"])
        for document in documents
    )
