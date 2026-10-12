"""Read and patch stored chunks in real MongoDB Atlas, as a refresh does. Excluded from the default
run.

    pytest retrieval/tests -m integration

Chunks are written with a placeholder vector, so no embedding request is made. Writes only to the
`code_search_test` database, which is dropped afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace

import pytest

from atlas_test_databases import empty_database
from retrieval.chunk_store import (
    CHUNKS_COLLECTION,
    CachedEmbedding,
    MongoChunkStore,
    StoredChunk,
    chunk_id_for,
)
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.clients import build_mongo_client
from retrieval.config import EMBEDDING_DIMENSIONS, MONGODB_URI_VARIABLE, load_environment
from retrieval.repository_cloner import CloneMetadata

pytestmark = pytest.mark.integration

TEST_DATABASE = "code_search_test"

METADATA = CloneMetadata(
    repository="integration/test",
    version="main",
    commit_id="a" * 40,
    license_spdx_id="MIT",
    license_name="MIT License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-10-11T00:00:00+00:00",
    license_path="LICENSE",
)

PLACEHOLDER_EMBEDDING = CachedEmbedding(
    vector=[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1), is_truncated=False
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


def _chunk(name: str, start_line: int, calls: tuple[str, ...] = ()) -> CodeChunk:
    return CodeChunk(
        file_path="pkg/app.py",
        start_line=start_line,
        end_line=start_line + 1,
        kind=ChunkKind.FUNCTION,
        name=name,
        qualified_name=name,
        parent_class=None,
        text=f"def {name}():\n    pass",
        calls=calls,
    )


def _store(store: MongoChunkStore, metadata: CloneMetadata, chunks: list[CodeChunk]) -> None:
    store.upsert_chunks(
        [
            StoredChunk(
                chunk_id=chunk_id_for(metadata, chunk),
                metadata=metadata,
                chunk=chunk,
                embedding_key=f"key-{chunk.name}",
                embedding=PLACEHOLDER_EMBEDDING,
            )
            for chunk in chunks
        ]
    )


def test_calls_are_read_without_vectors_and_updated_in_place(test_database):
    store = MongoChunkStore(test_database)
    caller = _chunk("run", 1, calls=("pkg/app.py::helper",))
    helper = _chunk("helper", 5)
    _store(store, METADATA, [caller, helper])
    caller_id = chunk_id_for(METADATA, caller)
    helper_id = chunk_id_for(METADATA, helper)

    assert store.find_chunk_calls("integration/test", "main") == {
        caller_id: ["pkg/app.py::helper"],
        helper_id: [],
    }
    store.update_chunk_calls({caller_id: []})

    assert store.find_chunk_calls("integration/test", "main")[caller_id] == []
    document = test_database[CHUNKS_COLLECTION].find_one({"_id": caller_id})
    assert document["text"] == caller.text
    assert document["embedding"].as_vector().data[0] == 1.0


def test_the_new_commit_is_set_on_one_version_only(test_database):
    store = MongoChunkStore(test_database)
    other_version = replace(METADATA, version="1.0.0")
    _store(store, METADATA, [_chunk("run", 1), _chunk("helper", 5)])
    _store(store, other_version, [_chunk("run", 1)])

    store.set_commit_id("integration/test", "main", "b" * 40)

    chunks = test_database[CHUNKS_COLLECTION]
    assert sorted(document["commit_id"] for document in chunks.find({"version": "main"})) == [
        "b" * 40,
        "b" * 40,
    ]
    assert chunks.find_one({"version": "1.0.0"})["commit_id"] == "a" * 40
