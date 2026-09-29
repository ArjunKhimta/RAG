"""Search real Atlas indexes with real Gemini embeddings. Excluded from the default run.

    pytest retrieval/tests -m integration

A free Atlas cluster allows three search indexes, and production already uses two, so these tests
use the production indexes rather than creating their own. Their chunks are stored under a unique
`integration-test/<random>` repository, which the search filters keep out of real searches, and
are deleted afterwards; leftovers from an interrupted run are deleted first. Costs eight embedding
requests: seven chunks and one question.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator

import pytest
from pymongo.collection import Collection

from retrieval.chunk_store import CHUNKS_COLLECTION, REPOSITORIES_COLLECTION, MongoChunkStore
from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_API_KEY_VARIABLE,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    MONGODB_URI_VARIABLE,
    VECTOR_INDEX_NAME,
    load_environment,
)
from retrieval.embedders import GeminiDocumentEmbedder, GeminiQueryEmbedder
from retrieval.indexer import index_chunks
from retrieval.keyword_search import KeywordSearchOptions, search_chunks_by_keywords
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import CloneMetadata
from retrieval.search_indexes import (
    EMBEDDING_FIELD,
    SearchIndexChange,
    ensure_keyword_index,
    ensure_vector_index,
    require_queryable_index,
)
from retrieval.search_results import SearchRefusedError
from retrieval.vector_search import SearchOptions, search_chunks

pytestmark = pytest.mark.integration

TEST_REPOSITORY_PREFIX = "integration-test/"

SEARCHED_VERSION = "1.0.0"

OTHER_VERSION = "2.0.0"

READY_TIMEOUT_SECONDS = 180

POLL_INTERVAL_SECONDS = 2

QUESTION = "add two numbers together and return the sum"

CHUNK_SOURCES = {
    "add_numbers": (
        "pkg/arithmetic.py",
        ChunkKind.FUNCTION,
        "def add_numbers(first_number, second_number):\n    return first_number + second_number",
        False,
    ),
    "read_config_file": (
        "pkg/config.py",
        ChunkKind.FUNCTION,
        "def read_config_file(path):\n    with open(path) as config_file:\n"
        "        return json.load(config_file)",
        False,
    ),
    "send_email_message": (
        "pkg/mail.py",
        ChunkKind.FUNCTION,
        "def send_email_message(recipient, subject, body):\n"
        "    smtp_connection.sendmail(SENDER, recipient, f'Subject: {subject}\\n\\n{body}')",
        False,
    ),
    "add_url_rule": (
        "pkg/routing.py",
        ChunkKind.FUNCTION,
        "def add_url_rule(rule, endpoint, view_func):\n"
        "    url_map.add(Rule(rule, endpoint=endpoint))\n"
        "    view_functions[endpoint] = view_func",
        False,
    ),
    "register_index_route": (
        "pkg/views.py",
        ChunkKind.FUNCTION,
        "def register_index_route(app):\n    app.add_url_rule('/', 'index', show_index)",
        False,
    ),
    "AppContext": (
        "pkg/ctx.py",
        ChunkKind.CLASS,
        "class AppContext:\n    def __init__(self, app):\n        self.app = app",
        False,
    ),
    "test_read_config_file": (
        "tests/test_config.py",
        ChunkKind.FUNCTION,
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
def test_repository() -> str:
    return f"{TEST_REPOSITORY_PREFIX}{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def chunks_collection(test_repository) -> Iterator[Collection]:
    mongo_client = build_mongo_client()
    database = mongo_client[MONGODB_DATABASE]
    _delete_test_repositories(database, {"$regex": f"^{TEST_REPOSITORY_PREFIX}"})
    try:
        collection = database[CHUNKS_COLLECTION]
        store = MongoChunkStore(database)
        store.ensure_indexes()
        ensure_vector_index(collection, EMBEDDING_DIMENSIONS)
        ensure_keyword_index(collection)
        embedder = GeminiDocumentEmbedder(build_gemini_client())
        chunks = [_chunk(name) for name in CHUNK_SOURCES]
        for version in (SEARCHED_VERSION, OTHER_VERSION):
            metadata = _metadata(test_repository, version)
            index_chunks(metadata, chunks, embedder, store, RateLimiter(10, 25_000))
        yield collection
    finally:
        _delete_test_repositories(database, test_repository)
        mongo_client.close()


@pytest.fixture(scope="module")
def query_vector() -> list[float]:
    return GeminiQueryEmbedder(build_gemini_client()).embed_query(QUESTION)


@pytest.fixture(scope="module")
def searchable_collection(chunks_collection, test_repository, query_vector) -> Collection:
    expected_count = 2 * len(CHUNK_SOURCES)
    _wait_until(
        lambda: _is_queryable(chunks_collection, VECTOR_INDEX_NAME)
        and _is_queryable(chunks_collection, KEYWORD_INDEX_NAME)
    )
    _wait_until(
        lambda: _vector_indexed_count(chunks_collection, test_repository, query_vector)
        == expected_count
        and _keyword_indexed_count(chunks_collection, test_repository) == expected_count
    )
    return chunks_collection


def test_ensuring_existing_indexes_again_changes_nothing(searchable_collection):
    assert ensure_vector_index(searchable_collection, EMBEDDING_DIMENSIONS) == (
        SearchIndexChange.UNCHANGED
    )
    assert ensure_keyword_index(searchable_collection) == SearchIndexChange.UNCHANGED


def test_vector_results_come_only_from_the_requested_version_best_first(
    searchable_collection, test_repository, query_vector
):
    results = search_chunks(
        searchable_collection, query_vector, test_repository, SEARCHED_VERSION, SearchOptions()
    )

    searched_prefix = f"{test_repository}@{SEARCHED_VERSION}:"
    scores = [result.score for result in results]
    assert len(results) == len(CHUNK_SOURCES)
    assert all(result.chunk_id.startswith(searched_prefix) for result in results)
    assert scores == sorted(scores, reverse=True)
    assert results[0].qualified_name == "add_numbers"


def test_the_vector_limit_caps_the_number_of_results(
    searchable_collection, test_repository, query_vector
):
    results = search_chunks(
        searchable_collection,
        query_vector,
        test_repository,
        SEARCHED_VERSION,
        SearchOptions(limit=2),
    )

    assert len(results) == 2


def test_vector_search_can_exclude_test_file_chunks(
    searchable_collection, test_repository, query_vector
):
    results = search_chunks(
        searchable_collection,
        query_vector,
        test_repository,
        SEARCHED_VERSION,
        SearchOptions(exclude_tests=True),
    )

    assert len(results) == len(CHUNK_SOURCES) - 1
    assert not any(result.is_test_file for result in results)


def test_exact_vector_search_agrees_with_approximate_search_on_a_tiny_collection(
    searchable_collection, test_repository, query_vector
):
    approximate_results = search_chunks(
        searchable_collection, query_vector, test_repository, SEARCHED_VERSION, SearchOptions()
    )
    exact_results = search_chunks(
        searchable_collection,
        query_vector,
        test_repository,
        SEARCHED_VERSION,
        SearchOptions(exact=True),
    )

    approximate_ids = [result.chunk_id for result in approximate_results]
    exact_ids = [result.chunk_id for result in exact_results]
    assert approximate_ids == exact_ids


def test_an_exact_identifier_ranks_its_definition_above_its_callers(
    searchable_collection, test_repository
):
    results = _keyword_search(searchable_collection, test_repository, "add_url_rule")

    result_names = [result.qualified_name for result in results]
    assert result_names[0] == "add_url_rule"
    assert "register_index_route" in result_names


def test_parts_of_a_snake_case_identifier_find_it(searchable_collection, test_repository):
    results = _keyword_search(searchable_collection, test_repository, "url rule")

    assert results[0].qualified_name == "add_url_rule"


def test_part_of_a_camel_case_identifier_finds_it(searchable_collection, test_repository):
    results = _keyword_search(searchable_collection, test_repository, "context")

    assert results[0].qualified_name == "AppContext"


def test_keyword_results_come_only_from_the_requested_version(
    searchable_collection, test_repository
):
    results = _keyword_search(searchable_collection, test_repository, "add_url_rule rule")

    searched_prefix = f"{test_repository}@{SEARCHED_VERSION}:"
    assert results
    assert all(result.chunk_id.startswith(searched_prefix) for result in results)


def test_keyword_search_can_exclude_test_file_chunks(searchable_collection, test_repository):
    included_results = _keyword_search(searchable_collection, test_repository, "read_config_file")
    excluded_results = _keyword_search(
        searchable_collection, test_repository, "read_config_file", exclude_tests=True
    )

    assert any(result.is_test_file for result in included_results)
    assert excluded_results
    assert not any(result.is_test_file for result in excluded_results)


def _keyword_search(collection, repository, query, exclude_tests=False):
    return search_chunks_by_keywords(
        collection,
        query,
        repository,
        SEARCHED_VERSION,
        KeywordSearchOptions(exclude_tests=exclude_tests),
    )


def _delete_test_repositories(database, repository_condition) -> None:
    database[CHUNKS_COLLECTION].delete_many({"repository": repository_condition})
    database[REPOSITORIES_COLLECTION].delete_many({"repository": repository_condition})


def _is_queryable(collection, index_name) -> bool:
    try:
        require_queryable_index(collection, index_name)
    except SearchRefusedError:
        return False
    return True


def _vector_indexed_count(collection, repository, query_vector) -> int:
    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX_NAME,
                "path": EMBEDDING_FIELD,
                "queryVector": query_vector,
                "filter": {"repository": {"$eq": repository}},
                "exact": True,
                "limit": 100,
            }
        },
        {"$count": "indexed"},
    ]
    counts = list(collection.aggregate(pipeline))
    return counts[0]["indexed"] if counts else 0


def _keyword_indexed_count(collection, repository) -> int:
    pipeline = [
        {
            "$search": {
                "index": KEYWORD_INDEX_NAME,
                "compound": {"filter": [{"equals": {"path": "repository", "value": repository}}]},
            }
        },
        {"$count": "indexed"},
    ]
    counts = list(collection.aggregate(pipeline))
    return counts[0]["indexed"] if counts else 0


def _wait_until(condition) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError(f"Search indexes not ready within {READY_TIMEOUT_SECONDS} seconds")
        time.sleep(POLL_INTERVAL_SECONDS)


def _metadata(repository: str, version: str) -> CloneMetadata:
    return CloneMetadata(
        repository=repository,
        version=version,
        commit_id="0" * 40,
        license_spdx_id="MIT",
        license_name="MIT License",
        reported_size_bytes=1,
        checkout_size_bytes=1,
        cloned_at="2026-09-29T00:00:00+00:00",
    )


def _chunk(name: str) -> CodeChunk:
    file_path, kind, text, is_test_file = CHUNK_SOURCES[name]
    return CodeChunk(
        file_path=file_path,
        start_line=1,
        end_line=text.count("\n") + 1,
        kind=kind,
        name=name,
        qualified_name=name,
        parent_class=None,
        text=text,
        is_test_file=is_test_file,
    )
