"""Search real Atlas Vector Search with a real Gemini query. Excluded from the default run.

    pytest retrieval/tests -m integration

Writes only to a throwaway `code_search_vector_test_<random>` database, which is dropped
afterwards. Costs five embedding requests: four chunks and one question.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator

import pytest

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import GEMINI_API_KEY_VARIABLE, MONGODB_URI_VARIABLE, load_environment
from retrieval.embedders import GeminiDocumentEmbedder, GeminiQueryEmbedder
from retrieval.indexer import index_chunks
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import CloneMetadata
from retrieval.vector_search import (
    SearchOptions,
    SearchRefusedError,
    VectorIndexChange,
    ensure_vector_index,
    require_queryable_index,
    search_chunks,
)

pytestmark = pytest.mark.integration

INDEX_READY_TIMEOUT_SECONDS = 180

INDEX_POLL_INTERVAL_SECONDS = 5

REPOSITORY = "integration/vector"

SEARCHED_VERSION = "1.0.0"

OTHER_VERSION = "2.0.0"

QUESTION = "add two numbers together and return the sum"

CHUNK_SOURCES = {
    "add_numbers": (
        "pkg/arithmetic.py",
        "def add_numbers(first_number, second_number):\n    return first_number + second_number",
        False,
    ),
    "read_config_file": (
        "pkg/config.py",
        "def read_config_file(path):\n    with open(path) as config_file:\n"
        "        return json.load(config_file)",
        False,
    ),
    "send_email_message": (
        "pkg/mail.py",
        "def send_email_message(recipient, subject, body):\n"
        "    smtp_connection.sendmail(SENDER, recipient, f'Subject: {subject}\\n\\n{body}')",
        False,
    ),
    "test_read_config_file": (
        "tests/test_config.py",
        "def test_read_config_file(tmp_path):\n    assert read_config_file(tmp_path / 'c.json')",
        True,
    ),
}


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    for variable_name in (GEMINI_API_KEY_VARIABLE, MONGODB_URI_VARIABLE):
        if not os.environ.get(variable_name):
            pytest.skip(f"{variable_name} is not configured")


@pytest.fixture(scope="module")
def indexed_database() -> Iterator:
    mongo_client = build_mongo_client()
    database_name = f"code_search_vector_test_{uuid.uuid4().hex[:8]}"
    database = mongo_client[database_name]
    store = MongoChunkStore(database)
    store.ensure_indexes()
    embedder = GeminiDocumentEmbedder(build_gemini_client())
    chunks = [_chunk(name) for name in CHUNK_SOURCES]
    first_report = index_chunks(
        _metadata(SEARCHED_VERSION), chunks, embedder, store, RateLimiter(10, 25_000)
    )
    other_version_report = index_chunks(
        _metadata(OTHER_VERSION), chunks, embedder, store, RateLimiter(10, 25_000)
    )
    assert first_report.embedding_request_count == 1
    assert other_version_report.embedding_request_count == 0
    try:
        yield database
    finally:
        mongo_client.drop_database(database_name)
        mongo_client.close()


@pytest.fixture(scope="module")
def query_vector() -> list[float]:
    return GeminiQueryEmbedder(build_gemini_client()).embed_query(QUESTION)


@pytest.fixture(scope="module")
def chunks_collection(indexed_database):
    collection = indexed_database[CHUNKS_COLLECTION]
    first_change = ensure_vector_index(collection, 768)
    second_change = ensure_vector_index(collection, 768)
    assert first_change == VectorIndexChange.CREATED
    assert second_change == VectorIndexChange.UNCHANGED
    _wait_until_queryable(collection)
    return collection


def test_results_come_only_from_the_requested_version_best_first(chunks_collection, query_vector):
    results = search_chunks(
        chunks_collection, query_vector, REPOSITORY, SEARCHED_VERSION, SearchOptions()
    )

    searched_prefix = f"{REPOSITORY}@{SEARCHED_VERSION}:"
    scores = [result.score for result in results]
    assert len(results) == len(CHUNK_SOURCES)
    assert all(result.chunk_id.startswith(searched_prefix) for result in results)
    assert scores == sorted(scores, reverse=True)
    assert results[0].qualified_name == "add_numbers"


def test_the_limit_caps_the_number_of_results(chunks_collection, query_vector):
    results = search_chunks(
        chunks_collection, query_vector, REPOSITORY, SEARCHED_VERSION, SearchOptions(limit=2)
    )

    assert len(results) == 2


def test_excluding_tests_leaves_out_test_file_chunks(chunks_collection, query_vector):
    results = search_chunks(
        chunks_collection,
        query_vector,
        REPOSITORY,
        SEARCHED_VERSION,
        SearchOptions(exclude_tests=True),
    )

    assert len(results) == len(CHUNK_SOURCES) - 1
    assert not any(result.is_test_file for result in results)


def test_exact_search_agrees_with_approximate_search_on_a_tiny_collection(
    chunks_collection, query_vector
):
    approximate_results = search_chunks(
        chunks_collection, query_vector, REPOSITORY, SEARCHED_VERSION, SearchOptions()
    )
    exact_results = search_chunks(
        chunks_collection, query_vector, REPOSITORY, SEARCHED_VERSION, SearchOptions(exact=True)
    )

    approximate_ids = [result.chunk_id for result in approximate_results]
    exact_ids = [result.chunk_id for result in exact_results]
    assert approximate_ids == exact_ids


def _wait_until_queryable(collection) -> None:
    deadline = time.monotonic() + INDEX_READY_TIMEOUT_SECONDS
    while True:
        try:
            require_queryable_index(collection)
            return
        except SearchRefusedError:
            if time.monotonic() > deadline:
                raise
            time.sleep(INDEX_POLL_INTERVAL_SECONDS)


def _metadata(version: str) -> CloneMetadata:
    return CloneMetadata(
        repository=REPOSITORY,
        version=version,
        commit_id="0" * 40,
        license_spdx_id="MIT",
        license_name="MIT License",
        reported_size_bytes=1,
        checkout_size_bytes=1,
        cloned_at="2026-09-29T00:00:00+00:00",
    )


def _chunk(name: str) -> CodeChunk:
    file_path, text, is_test_file = CHUNK_SOURCES[name]
    return CodeChunk(
        file_path=file_path,
        start_line=1,
        end_line=text.count("\n") + 1,
        kind=ChunkKind.FUNCTION,
        name=name,
        qualified_name=name,
        parent_class=None,
        text=text,
        is_test_file=is_test_file,
    )
