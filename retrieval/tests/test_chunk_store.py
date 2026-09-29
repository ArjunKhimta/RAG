from __future__ import annotations

import pytest
from bson import BSON
from bson.binary import Binary

from retrieval.chunk_store import (
    CachedEmbedding,
    IndexedVersion,
    StoredChunk,
    cached_embedding_from,
    chunk_document,
    chunk_id_for,
    repository_document,
)
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.repository_cloner import CloneMetadata

METADATA = CloneMetadata(
    repository="pallets/flask",
    version="3.1.3",
    commit_id="22d924701a6ae2e4cd01e9a15bbaf3946094af65",
    license_spdx_id="BSD-3-Clause",
    license_name='BSD 3-Clause "New" or "Revised" License',
    reported_size_bytes=100,
    checkout_size_bytes=50,
    cloned_at="2026-09-29T00:00:00+00:00",
)

CHUNK = CodeChunk(
    file_path="src/flask/app.py",
    start_line=546,
    end_line=636,
    kind=ChunkKind.METHOD,
    name="run",
    qualified_name="Flask.run",
    parent_class="Flask",
    text="    def run(self):\n        ...",
    is_test_file=False,
    contains_redaction=True,
)

VECTOR = [0.6, -0.8, 0.0]


def test_chunk_ids_are_readable_and_identify_repository_version_location_and_part():
    assert chunk_id_for(METADATA, CHUNK) == "pallets/flask@3.1.3:src/flask/app.py:546:method:1"


def test_a_chunk_document_holds_every_field_and_a_binary_float32_vector():
    stored = _stored_chunk()

    document = chunk_document(stored)

    assert document["_id"] == stored.chunk_id
    assert document["repository"] == "pallets/flask"
    assert document["version"] == "3.1.3"
    assert document["kind"] == "method"
    assert document["qualified_name"] == "Flask.run"
    assert document["contains_redaction"] is True
    assert document["embedding_key"] == "key-1"
    assert isinstance(document["embedding"], Binary)
    assert len(document["embedding"]) == 2 + 4 * len(VECTOR)


def test_a_vector_survives_a_round_trip_through_bson():
    encoded_document = BSON.encode(chunk_document(_stored_chunk()))

    cached_embedding = cached_embedding_from(BSON(encoded_document).decode())

    assert cached_embedding.vector == pytest.approx(VECTOR)
    assert cached_embedding.is_truncated is False


def test_a_repository_document_records_the_license_shown_with_answers():
    document = repository_document(
        IndexedVersion(
            metadata=METADATA,
            embedding_model="gemini-embedding-001",
            embedding_dimensions=768,
            chunk_count=1009,
        )
    )

    assert document["_id"] == "pallets/flask@3.1.3"
    assert document["license_spdx_id"] == "BSD-3-Clause"
    assert document["chunk_count"] == 1009
    assert document["embedding_dimensions"] == 768


def _stored_chunk() -> StoredChunk:
    return StoredChunk(
        chunk_id=chunk_id_for(METADATA, CHUNK),
        metadata=METADATA,
        chunk=CHUNK,
        embedding_key="key-1",
        embedding=CachedEmbedding(vector=VECTOR, is_truncated=False),
    )
