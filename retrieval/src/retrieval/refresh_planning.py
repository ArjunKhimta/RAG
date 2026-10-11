"""Decide whether indexing a version can patch what is already stored, and which chunks to keep.

A version indexed before can be patched only when everything stored describes one known state:
its record exists, it was embedded with the same model and size, its fingerprints exist, describe
the record's commit, and were made with the current index format. Anything else is indexed in full,
and the reason is reported. Indexing in full still reuses cached embeddings, so it costs Atlas
writes, not Gemini quota, for chunks that did not change.

When patching, the chunks of files the fingerprints show unchanged are kept as stored: their bytes
are identical, so their text, lines, and embeddings are too. Two things can still differ. Their
call links, because the call graph crosses files: a change in one file can resolve a call in
another differently. And their presence: a chunk that should be stored but is not (deleted by
hand, say) is written again rather than trusted, so a gap in the index never survives a refresh.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from retrieval.chunk_store import chunk_id_for
from retrieval.chunker import CodeChunk
from retrieval.embedders import EmbeddingIdentity
from retrieval.fingerprint_comparison import FingerprintComparison, compare_fingerprints
from retrieval.fingerprint_store import StoredFingerprints
from retrieval.fingerprints import VersionFingerprints
from retrieval.indexer import KeptChunks
from retrieval.repository_cloner import CloneMetadata


class FullIndexReason(StrEnum):
    NOT_INDEXED_BEFORE = "not indexed before"
    OTHER_EMBEDDING = "indexed with another embedding model or size"
    NO_FINGERPRINTS = "no fingerprints stored"
    OTHER_FORMAT = "fingerprints made with another index format"
    OTHER_COMMIT = "fingerprints describe another commit"


@dataclass(frozen=True)
class RefreshPlan:
    """Either a reason to index in full, or what changed and which stored chunks to keep."""

    full_index_reason: FullIndexReason | None
    comparison: FingerprintComparison | None
    kept: KeptChunks | None


class StoredVersionReader(Protocol):
    def find_repository_record(self, repository: str, version: str) -> dict[str, Any] | None: ...

    def find_chunk_calls(self, repository: str, version: str) -> dict[str, list[str]]: ...


class StoredFingerprintReader(Protocol):
    def find(self, repository: str, version: str) -> StoredFingerprints | None: ...


def plan_refresh(
    chunk_store: StoredVersionReader,
    fingerprint_store: StoredFingerprintReader,
    metadata: CloneMetadata,
    chunks: list[CodeChunk],
    fingerprints: VersionFingerprints,
    embedder: EmbeddingIdentity,
    format_version: int,
) -> RefreshPlan:
    repository_record = chunk_store.find_repository_record(metadata.repository, metadata.version)
    stored_fingerprints = fingerprint_store.find(metadata.repository, metadata.version)
    usable = usable_fingerprints_or_reason(
        repository_record, stored_fingerprints, embedder, format_version
    )
    if isinstance(usable, FullIndexReason):
        return RefreshPlan(full_index_reason=usable, comparison=None, kept=None)
    comparison = compare_fingerprints(usable.fingerprints, fingerprints)
    stored_calls = chunk_store.find_chunk_calls(metadata.repository, metadata.version)
    chunk_ids = [chunk_id_for(metadata, chunk) for chunk in chunks]
    kept = plan_kept_chunks(chunks, chunk_ids, comparison, stored_calls)
    return RefreshPlan(full_index_reason=None, comparison=comparison, kept=kept)


def usable_fingerprints_or_reason(
    repository_record: dict[str, Any] | None,
    stored_fingerprints: StoredFingerprints | None,
    embedder: EmbeddingIdentity,
    format_version: int,
) -> StoredFingerprints | FullIndexReason:
    """Return the stored fingerprints if the version can be patched, else why it cannot."""
    if repository_record is None:
        return FullIndexReason.NOT_INDEXED_BEFORE
    same_model = repository_record.get("embedding_model") == embedder.model_id
    same_dimensions = repository_record.get("embedding_dimensions") == embedder.dimensions
    if not (same_model and same_dimensions):
        return FullIndexReason.OTHER_EMBEDDING
    if stored_fingerprints is None:
        return FullIndexReason.NO_FINGERPRINTS
    if stored_fingerprints.format_version != format_version:
        return FullIndexReason.OTHER_FORMAT
    if stored_fingerprints.commit_id != repository_record.get("commit_id"):
        return FullIndexReason.OTHER_COMMIT
    return stored_fingerprints


def plan_kept_chunks(
    chunks: list[CodeChunk],
    chunk_ids: list[str],
    comparison: FingerprintComparison,
    stored_calls: dict[str, list[str]],
) -> KeptChunks:
    """Keep each chunk of an unchanged file that is stored, with new calls where they changed."""
    rewritten_files = set(comparison.added) | set(comparison.changed)
    kept_ids: set[str] = set()
    calls_to_update: dict[str, list[str]] = {}
    for chunk, chunk_id in zip(chunks, chunk_ids, strict=True):
        if chunk.file_path in rewritten_files or chunk_id not in stored_calls:
            continue
        kept_ids.add(chunk_id)
        current_calls = list(chunk.calls)
        if current_calls != stored_calls[chunk_id]:
            calls_to_update[chunk_id] = current_calls
    return KeptChunks(chunk_ids=frozenset(kept_ids), calls_to_update=calls_to_update)
