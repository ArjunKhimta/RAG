"""Embed with the real Gemini API and store in real MongoDB Atlas. Excluded from the default run.

    pytest retrieval/tests -m integration

Writes only to the `code_search_test` database, which is dropped afterwards.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator

import pytest
from bson.binary import BinaryVectorDtype

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_API_KEY_VARIABLE,
    MONGODB_URI_VARIABLE,
    load_environment,
)
from retrieval.embedders import GeminiDocumentEmbedder
from retrieval.indexer import index_chunks
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import CloneMetadata

pytestmark = pytest.mark.integration

TEST_DATABASE = "code_search_test"

BATCH_SIZE_TO_CONFIRM = 100

METADATA = CloneMetadata(
    repository="integration/test",
    version="1.0.0",
    commit_id="0" * 40,
    license_spdx_id="MIT",
    license_name="MIT License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-09-29T00:00:00+00:00",
)


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    for variable_name in (GEMINI_API_KEY_VARIABLE, MONGODB_URI_VARIABLE):
        if not os.environ.get(variable_name):
            pytest.skip(f"{variable_name} is not configured")


@pytest.fixture
def test_database() -> Iterator:
    mongo_client = build_mongo_client()
    mongo_client.drop_database(TEST_DATABASE)
    yield mongo_client[TEST_DATABASE]
    mongo_client.drop_database(TEST_DATABASE)
    mongo_client.close()


def test_a_batch_of_one_hundred_texts_returns_one_hundred_unit_vectors():
    embedder = GeminiDocumentEmbedder(build_gemini_client())
    texts = [
        f"def function_{index}():\n    return {index}" for index in range(BATCH_SIZE_TO_CONFIRM)
    ]

    vectors = embedder.embed_documents(texts)

    assert len(vectors) == BATCH_SIZE_TO_CONFIRM
    assert all(len(vector) == EMBEDDING_DIMENSIONS for vector in vectors)
    assert all(math.isclose(math.hypot(*vector), 1.0, rel_tol=1e-6) for vector in vectors)


def test_indexing_stores_binary_vectors_and_a_second_run_uses_the_cache(test_database):
    store = MongoChunkStore(test_database)
    store.ensure_indexes()
    embedder = GeminiDocumentEmbedder(build_gemini_client())
    chunks = [_chunk(index) for index in range(3)]

    first_report = index_chunks(METADATA, chunks, embedder, store, RateLimiter(5, 25_000))
    second_report = index_chunks(METADATA, chunks, embedder, store, RateLimiter(5, 25_000))

    stored_document = test_database[CHUNKS_COLLECTION].find_one({"name": "function_0"})
    stored_vector = stored_document["embedding"].as_vector()
    assert first_report.embedding_request_count == 1
    assert second_report.embedding_request_count == 0
    assert second_report.cache_hit_count == 3
    assert stored_vector.dtype == BinaryVectorDtype.FLOAT32
    assert len(stored_vector.data) == EMBEDDING_DIMENSIONS
    assert test_database[CHUNKS_COLLECTION].count_documents({}) == 3


def _chunk(index: int) -> CodeChunk:
    return CodeChunk(
        file_path="pkg/module.py",
        start_line=index * 10 + 1,
        end_line=index * 10 + 2,
        kind=ChunkKind.FUNCTION,
        name=f"function_{index}",
        qualified_name=f"function_{index}",
        parent_class=None,
        text=f"def function_{index}():\n    return {index}",
    )
