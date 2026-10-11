"""A repository version's overview: its source files and how they call each other.

The overview is read from the call graph stored at indexing time, so it costs no Gemini request
and no embedding. Each source chunk stores the symbols it calls (`file_path::qualified_name`), and
an edge from one function to another is counted once, as the call graph counts it. Grouping the
edges by the caller's file and the callee's file gives file-to-file links; edges inside one file
are counted for that file instead. Test files are left out, as the call graph leaves them out.

Classes and functions are counted from the first part of each definition, so a long function
split into parts counts once. Module-level chunks are not definitions, but their files are listed.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pymongo.database import Database

from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore, require_license_path
from retrieval.chunker import ChunkKind
from retrieval.code_graph import SYMBOL_SEPARATOR
from retrieval.graph_expansion import require_call_graph
from retrieval.search_results import require_indexed_version

OVERVIEW_PROJECTION = {"_id": 0, "file_path": 1, "kind": 1, "part_number": 1, "calls": 1}

FUNCTION_KINDS = frozenset({ChunkKind.FUNCTION.value, ChunkKind.METHOD.value})

FIRST_PART = 1


@dataclass(frozen=True)
class FileOverview:
    path: str
    class_count: int
    function_count: int
    calls_within: int
    calls_out: int
    calls_in: int


@dataclass(frozen=True)
class FileLink:
    caller_file: str
    callee_file: str
    call_count: int


@dataclass(frozen=True)
class RepositoryOverview:
    files: list[FileOverview]
    links: list[FileLink]

    @property
    def call_count(self) -> int:
        return self.calls_between_files + self.calls_within_files

    @property
    def calls_between_files(self) -> int:
        return sum(link.call_count for link in self.links)

    @property
    def calls_within_files(self) -> int:
        return sum(file.calls_within for file in self.files)


@dataclass(frozen=True)
class OverviewRun:
    overview: RepositoryOverview
    repository_record: dict[str, Any]
    timings: dict[str, float]


def file_of(symbol: str) -> str:
    """The file part of a symbol; qualified names never contain the separator, so split last."""
    return symbol.rpartition(SYMBOL_SEPARATOR)[0]


def summarize_chunks(documents: Iterable[dict[str, Any]]) -> RepositoryOverview:
    paths: set[str] = set()
    class_counts: Counter[str] = Counter()
    function_counts: Counter[str] = Counter()
    calls_within: Counter[str] = Counter()
    calls_out: Counter[str] = Counter()
    calls_in: Counter[str] = Counter()
    link_counts: Counter[tuple[str, str]] = Counter()

    for document in documents:
        caller_file = document["file_path"]
        paths.add(caller_file)
        if document.get("part_number", FIRST_PART) == FIRST_PART:
            if document["kind"] == ChunkKind.CLASS.value:
                class_counts[caller_file] += 1
            elif document["kind"] in FUNCTION_KINDS:
                function_counts[caller_file] += 1
        for callee in document.get("calls", []):
            callee_file = file_of(callee)
            paths.add(callee_file)
            if callee_file == caller_file:
                calls_within[caller_file] += 1
                continue
            link_counts[(caller_file, callee_file)] += 1
            calls_out[caller_file] += 1
            calls_in[callee_file] += 1

    files = [
        FileOverview(
            path=path,
            class_count=class_counts[path],
            function_count=function_counts[path],
            calls_within=calls_within[path],
            calls_out=calls_out[path],
            calls_in=calls_in[path],
        )
        for path in sorted(paths)
    ]
    links = [
        FileLink(caller_file=caller, callee_file=callee, call_count=count)
        for (caller, callee), count in sorted(
            link_counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    return RepositoryOverview(files=files, links=links)


class RepositoryOverviewBuilder:
    """Builds overviews from the `chunks` collection; holds no state between requests."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def overview(self, repository: str, version: str) -> OverviewRun:
        timings: dict[str, float] = {}
        stage_started = time.perf_counter()
        repository_record = MongoChunkStore(self._database).find_repository_record(
            repository, version
        )
        require_indexed_version(repository_record, repository, version)
        require_license_path(repository_record, repository, version)
        require_call_graph(repository_record, repository, version)
        timings["checks"] = _milliseconds_since(stage_started)

        stage_started = time.perf_counter()
        documents = self._database[CHUNKS_COLLECTION].find(
            {"repository": repository, "version": version, "is_test_file": False},
            OVERVIEW_PROJECTION,
        )
        overview = summarize_chunks(documents)
        timings["summarize call graph"] = _milliseconds_since(stage_started)
        return OverviewRun(overview=overview, repository_record=repository_record, timings=timings)


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * 1000
