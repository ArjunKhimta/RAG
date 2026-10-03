"""Expand sources through the call graph in real Atlas. Excluded from the default run.

    pytest retrieval/tests -m integration

The chunks of a tiny repository are chunked, given their calls by the real call graph, and written
straight to the chunks collection with a placeholder vector, so no embedding request is made and
no search index is needed: `$graphLookup` uses the ordinary `symbol` and `calls` indexes. They are
stored under a unique `integration-test/<random>` repository in two versions, and deleted
afterwards; leftovers from an interrupted run are deleted first.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import replace

import pytest
from pymongo.collection import Collection
from pymongo.database import Database

from retrieval.call_sites import find_file_calls
from retrieval.chunk_store import (
    CHUNKS_COLLECTION,
    REPOSITORIES_COLLECTION,
    CachedEmbedding,
    IndexedVersion,
    MongoChunkStore,
    StoredChunk,
    chunk_id_for,
)
from retrieval.chunker import CodeChunk, chunk_python_source
from retrieval.clients import build_mongo_client
from retrieval.code_graph import attach_calls, build_call_graph, edge_count_of, symbol_for
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    MONGODB_DATABASE,
    MONGODB_URI_VARIABLE,
    load_environment,
)
from retrieval.graph_expansion import NeighborRelation, find_graph_neighbors, require_call_graph
from retrieval.repository_cloner import CloneMetadata
from retrieval.search_results import SearchResult

pytestmark = pytest.mark.integration

TEST_REPOSITORY_PREFIX = "integration-test/"

SEARCHED_VERSION = "1.0.0"

OTHER_VERSION = "2.0.0"

APP_FILE = "pkg/app.py"

CLI_FILE = "pkg/cli.py"

TEST_FILE = "tests/test_app.py"

SOURCE_FILES = {
    APP_FILE: (
        "from typing import overload\n"
        "\n"
        "\n"
        "class App:\n"
        "    def run(self):\n"
        "        self.prepare()\n"
        '        return locate("app")\n'
        "\n"
        "    def prepare(self):\n"
        "        return 1\n"
        "\n"
        "\n"
        "@overload\n"
        "def locate(name: str) -> str: ...\n"
        "\n"
        "\n"
        "def locate(name):\n"
        "    return name\n"
    ),
    CLI_FILE: "from .app import App\n\n\ndef main():\n    return App().run()\n",
}

TEST_SOURCE = "from pkg.app import App\n\n\ndef test_run():\n    assert App().run()\n"

PLACEHOLDER_VECTOR = [1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    if not os.environ.get(MONGODB_URI_VARIABLE):
        pytest.skip(f"{MONGODB_URI_VARIABLE} is not configured")


@pytest.fixture(scope="module")
def test_repository() -> str:
    return f"{TEST_REPOSITORY_PREFIX}{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def graph_chunks() -> list[CodeChunk]:
    source_chunks: list[CodeChunk] = []
    file_calls = []
    for file_path, text in SOURCE_FILES.items():
        source_chunks.extend(chunk_python_source(file_path, text.encode()))
        file_calls.append(find_file_calls(file_path, text.encode()))
    graph = build_call_graph(source_chunks, file_calls)
    test_chunks = [
        _planted_test_chunk(chunk)
        for chunk in chunk_python_source(TEST_FILE, TEST_SOURCE.encode())
    ]
    return [*attach_calls(source_chunks, graph), *test_chunks]


def _planted_test_chunk(chunk: CodeChunk) -> CodeChunk:
    """Mark a test chunk, and give `test_run` the call the graph leaves out for test files."""
    calls: tuple[str, ...] = ()
    if chunk.qualified_name == "test_run":
        calls = (symbol_for(APP_FILE, "App.run"),)
    return replace(chunk, is_test_file=True, calls=calls)


@pytest.fixture(scope="module")
def database(test_repository, graph_chunks) -> Iterator[Database]:
    mongo_client = build_mongo_client()
    database = mongo_client[MONGODB_DATABASE]
    _delete_test_repositories(database, {"$regex": f"^{TEST_REPOSITORY_PREFIX}"})
    try:
        store = MongoChunkStore(database)
        store.ensure_indexes()
        for version in (SEARCHED_VERSION, OTHER_VERSION):
            metadata = _metadata(test_repository, version)
            store.upsert_chunks(_stored_chunks(metadata, graph_chunks))
            store.upsert_repository(
                IndexedVersion(
                    metadata=metadata,
                    embedding_model="placeholder",
                    embedding_dimensions=EMBEDDING_DIMENSIONS,
                    chunk_count=len(graph_chunks),
                    call_graph_edge_count=edge_count_of(graph_chunks),
                )
            )
        yield database
    finally:
        _delete_test_repositories(database, test_repository)
        mongo_client.close()


@pytest.fixture(scope="module")
def chunks_collection(database) -> Collection:
    return database[CHUNKS_COLLECTION]


def test_a_source_gets_its_callees_then_its_callers_from_its_own_version_only(
    chunks_collection, test_repository, graph_chunks
):
    source = _source_result(test_repository, graph_chunks, "App.run")

    neighbors = find_graph_neighbors(
        chunks_collection, [source], test_repository, SEARCHED_VERSION
    )

    assert [
        (neighbor.result.qualified_name, neighbor.result.start_line, neighbor.relation)
        for neighbor in neighbors
    ] == [
        ("App.prepare", 9, NeighborRelation.CALLEE),
        ("locate", 17, NeighborRelation.CALLEE),
        ("main", 4, NeighborRelation.CALLER),
    ]
    searched_prefix = f"{test_repository}@{SEARCHED_VERSION}:"
    assert all(neighbor.result.chunk_id.startswith(searched_prefix) for neighbor in neighbors)
    assert all(neighbor.source_chunk_id == source.chunk_id for neighbor in neighbors)
    assert not any(neighbor.result.is_test_file for neighbor in neighbors)


def test_a_neighbor_that_is_already_a_source_is_not_repeated(
    chunks_collection, test_repository, graph_chunks
):
    sources = [
        _source_result(test_repository, graph_chunks, "App.run"),
        _source_result(test_repository, graph_chunks, "main"),
    ]

    neighbors = find_graph_neighbors(chunks_collection, sources, test_repository, SEARCHED_VERSION)

    neighbor_names = [neighbor.result.qualified_name for neighbor in neighbors]
    assert neighbor_names == ["App.prepare", "locate", "App"]


def test_the_stored_version_record_says_it_has_a_call_graph(database, test_repository):
    record = MongoChunkStore(database).find_repository_record(test_repository, SEARCHED_VERSION)

    assert record is not None
    real_edge_count = 4
    planted_test_edge_count = 1
    assert record["call_graph_edge_count"] == real_edge_count + planted_test_edge_count
    require_call_graph(record, test_repository, SEARCHED_VERSION)


def _source_result(repository: str, chunks: list[CodeChunk], qualified_name: str) -> SearchResult:
    chunk = next(chunk for chunk in chunks if chunk.qualified_name == qualified_name)
    return SearchResult(
        chunk_id=chunk_id_for(_metadata(repository, SEARCHED_VERSION), chunk),
        file_path=chunk.file_path,
        start_line=chunk.start_line,
        end_line=chunk.end_line,
        kind=str(chunk.kind),
        qualified_name=chunk.qualified_name,
        signature=chunk.signature,
        part_number=chunk.part_number,
        part_count=chunk.part_count,
        is_test_file=chunk.is_test_file,
        text=chunk.text,
        score=1.0,
    )


def _stored_chunks(metadata: CloneMetadata, chunks: list[CodeChunk]) -> list[StoredChunk]:
    return [
        StoredChunk(
            chunk_id=chunk_id_for(metadata, chunk),
            metadata=metadata,
            chunk=chunk,
            embedding_key=f"integration-placeholder-{position}",
            embedding=CachedEmbedding(vector=PLACEHOLDER_VECTOR, is_truncated=False),
        )
        for position, chunk in enumerate(chunks)
    ]


def _delete_test_repositories(database, repository_condition) -> None:
    database[CHUNKS_COLLECTION].delete_many({"repository": repository_condition})
    database[REPOSITORIES_COLLECTION].delete_many({"repository": repository_condition})


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
