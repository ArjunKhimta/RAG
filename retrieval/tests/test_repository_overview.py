from __future__ import annotations

import pytest

from retrieval.repository_overview import (
    OVERVIEW_PROJECTION,
    RepositoryOverviewBuilder,
    file_of,
    summarize_chunks,
)
from retrieval.search_results import SearchRefusedError

APP = "src/flask/app.py"
HELPERS = "src/flask/helpers.py"
SESSIONS = "src/flask/sessions.py"


def chunk(file_path, kind, calls=(), part_number=1):
    return {"file_path": file_path, "kind": kind, "part_number": part_number, "calls": list(calls)}


def test_file_of_keeps_everything_before_the_last_separator():
    assert file_of("src/flask/app.py::Flask.run") == "src/flask/app.py"
    assert file_of("odd::name.py::helper") == "odd::name.py"


def test_calls_between_files_become_links_and_calls_inside_a_file_stay_with_it():
    overview = summarize_chunks(
        [
            chunk(APP, "method", calls=[f"{HELPERS}::url_for", f"{HELPERS}::send_file"]),
            chunk(APP, "method", calls=[f"{HELPERS}::url_for", f"{APP}::Flask.make_response"]),
            chunk(SESSIONS, "method", calls=[f"{HELPERS}::url_for"]),
            chunk(HELPERS, "function"),
        ]
    )

    links = [(link.caller_file, link.callee_file, link.call_count) for link in overview.links]
    assert links == [(APP, HELPERS, 3), (SESSIONS, HELPERS, 1)]
    by_path = {file.path: file for file in overview.files}
    assert by_path[APP].calls_within == 1
    assert by_path[APP].calls_out == 3
    assert by_path[HELPERS].calls_in == 4
    assert by_path[SESSIONS].calls_out == 1
    assert overview.calls_between_files == 4
    assert overview.calls_within_files == 1
    assert overview.call_count == 5


def test_totals_equal_the_number_of_stored_call_edges():
    documents = [
        chunk(APP, "method", calls=[f"{HELPERS}::a", f"{APP}::b", f"{SESSIONS}::c"]),
        chunk(SESSIONS, "function", calls=[f"{SESSIONS}::d"]),
        chunk(HELPERS, "function", calls=[]),
    ]

    overview = summarize_chunks(documents)

    assert overview.call_count == sum(len(document["calls"]) for document in documents)


def test_definitions_count_once_however_many_parts_they_have():
    overview = summarize_chunks(
        [
            chunk(APP, "class"),
            chunk(APP, "class", part_number=2),
            chunk(APP, "method"),
            chunk(APP, "method", part_number=2),
            chunk(APP, "function"),
            chunk(APP, "module"),
        ]
    )

    only_file = overview.files[0]
    assert (only_file.class_count, only_file.function_count) == (1, 2)


def test_files_are_sorted_and_include_module_only_files_and_called_files():
    overview = summarize_chunks(
        [chunk(SESSIONS, "module"), chunk(APP, "function", calls=[f"{HELPERS}::url_for"])]
    )

    assert [file.path for file in overview.files] == [APP, HELPERS, SESSIONS]


def test_links_are_ordered_by_calls_then_by_files():
    overview = summarize_chunks(
        [
            chunk(SESSIONS, "function", calls=[f"{APP}::a"]),
            chunk(HELPERS, "function", calls=[f"{APP}::a"]),
            chunk(APP, "function", calls=[f"{HELPERS}::b", f"{HELPERS}::c"]),
        ]
    )

    assert [(link.caller_file, link.callee_file) for link in overview.links] == [
        (APP, HELPERS),
        (HELPERS, APP),
        (SESSIONS, APP),
    ]


def test_an_empty_version_has_no_files_or_links():
    overview = summarize_chunks([])

    assert overview.files == []
    assert overview.links == []
    assert overview.call_count == 0


class FakeRepositories:
    def __init__(self, record):
        self.record = record

    def find_one(self, query):
        if self.record is None:
            return None
        matches = all(self.record.get(key) == value for key, value in query.items())
        return self.record if matches else None


class FakeChunks:
    def __init__(self, documents):
        self.documents = documents
        self.queries = []

    def find(self, query, projection):
        self.queries.append((query, projection))
        return [
            document
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]


class FakeDatabase:
    def __init__(self, record, documents):
        self.collections = {
            "repositories": FakeRepositories(record),
            "chunks": FakeChunks(documents),
        }

    def __getitem__(self, name):
        return self.collections[name]


def flask_record(**overrides):
    record = {
        "repository": "pallets/flask",
        "version": "3.1.3",
        "commit_id": "22d924701a6ae2e4cd01e9a15bbaf3946094af65",
        "license_spdx_id": "BSD-3-Clause",
        "license_name": "BSD 3-Clause License",
        "license_path": "LICENSE.txt",
        "call_graph_edge_count": 1,
    }
    record.update(overrides)
    return record


def stored_chunk(file_path, kind, calls=(), is_test_file=False, version="3.1.3"):
    return {
        "repository": "pallets/flask",
        "version": version,
        "is_test_file": is_test_file,
        **chunk(file_path, kind, calls),
    }


def test_the_builder_reads_only_the_versions_source_chunks():
    database = FakeDatabase(
        flask_record(),
        [
            stored_chunk(APP, "function", calls=[f"{HELPERS}::url_for"]),
            stored_chunk("tests/test_basic.py", "function", is_test_file=True),
            stored_chunk(SESSIONS, "function", version="3.1.2"),
        ],
    )

    run = RepositoryOverviewBuilder(database).overview("pallets/flask", "3.1.3")

    assert [file.path for file in run.overview.files] == [APP, HELPERS]
    assert database["chunks"].queries == [
        (
            {"repository": "pallets/flask", "version": "3.1.3", "is_test_file": False},
            OVERVIEW_PROJECTION,
        )
    ]
    assert run.repository_record["commit_id"].startswith("22d9247")
    assert list(run.timings) == ["checks", "summarize call graph"]


def test_the_projection_never_reads_code_or_embeddings():
    assert set(OVERVIEW_PROJECTION) == {"_id", "file_path", "kind", "part_number", "calls"}
    assert OVERVIEW_PROJECTION["_id"] == 0


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (None, "has not finished indexing"),
        (flask_record(license_path=None), "before license files were recorded"),
        (
            {key: value for key, value in flask_record().items() if key != "call_graph_edge_count"},
            "before the call graph existed",
        ),
    ],
)
def test_the_builder_refuses_a_version_it_cannot_describe(record, expected):
    database = FakeDatabase(record, [])

    with pytest.raises(SearchRefusedError, match=expected):
        RepositoryOverviewBuilder(database).overview("pallets/flask", "3.1.3")

    assert database["chunks"].queries == []
