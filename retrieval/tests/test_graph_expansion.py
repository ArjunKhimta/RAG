from __future__ import annotations

from typing import Any

import pytest

from retrieval.graph_expansion import (
    NeighborRelation,
    build_expansion_pipeline,
    require_call_graph,
    select_neighbors,
)
from retrieval.search_results import SearchRefusedError

APP_FILE = "src/pkg/app.py"


def _document(
    qualified_name: str,
    start_line: int,
    file_path: str = APP_FILE,
    part_number: int = 1,
) -> dict[str, Any]:
    return {
        "_id": f"owner/repo@1.0:{file_path}:{start_line}:function:{part_number}",
        "file_path": file_path,
        "start_line": start_line,
        "end_line": start_line + 2,
        "kind": "function",
        "qualified_name": qualified_name,
        "symbol": f"{file_path}::{qualified_name}",
        "signature": None,
        "part_number": part_number,
        "part_count": 1,
        "is_test_file": False,
        "text": f"def {qualified_name}():\n    pass",
    }


def _expanded(source: dict[str, Any], callees: list, callers: list) -> dict[str, Any]:
    return {"_id": source["_id"], "callees": callees, "callers": callers}


def test_the_pipeline_looks_one_hop_each_way_within_the_version_and_skips_tests():
    pipeline = build_expansion_pipeline(["chunk-1"], "owner/repo", "1.0")

    assert pipeline[0] == {"$match": {"_id": {"$in": ["chunk-1"]}}}
    callee_lookup = pipeline[1]["$graphLookup"]
    caller_lookup = pipeline[2]["$graphLookup"]
    assert (callee_lookup["startWith"], callee_lookup["connectToField"]) == ("$calls", "symbol")
    assert (caller_lookup["startWith"], caller_lookup["connectToField"]) == ("$symbol", "calls")
    for lookup in (callee_lookup, caller_lookup):
        assert lookup["maxDepth"] == 0
        assert lookup["restrictSearchWithMatch"] == {
            "repository": "owner/repo",
            "version": "1.0",
            "is_test_file": False,
        }
    assert pipeline[-1] == {"$project": {"callees.embedding": 0, "callers.embedding": 0}}


def test_neighbors_follow_source_rank_with_callees_before_callers_and_no_score():
    first_source = _document("run", 10)
    second_source = _document("serve", 40)
    callee = _document("prepare", 20)
    caller = _document("main", 1, file_path="src/pkg/cli.py")
    documents = [
        _expanded(second_source, callees=[], callers=[caller]),
        _expanded(first_source, callees=[callee], callers=[]),
    ]

    neighbors = select_neighbors([first_source["_id"], second_source["_id"]], documents)

    assert [
        (neighbor.result.qualified_name, neighbor.relation, neighbor.source_chunk_id)
        for neighbor in neighbors
    ] == [
        ("prepare", NeighborRelation.CALLEE, first_source["_id"]),
        ("main", NeighborRelation.CALLER, second_source["_id"]),
    ]
    assert all(neighbor.result.score == 0.0 for neighbor in neighbors)


def test_sources_and_repeated_neighbors_are_left_out():
    first_source = _document("run", 10)
    second_source = _document("prepare", 20)
    shared_callee = _document("validate", 30)
    documents = [
        _expanded(first_source, callees=[second_source, shared_callee], callers=[]),
        _expanded(second_source, callees=[shared_callee], callers=[first_source]),
    ]

    neighbors = select_neighbors([first_source["_id"], second_source["_id"]], documents)

    assert [neighbor.result.qualified_name for neighbor in neighbors] == ["validate"]
    assert neighbors[0].source_chunk_id == first_source["_id"]


def test_a_callee_is_its_last_definition_and_only_its_first_part():
    source = _document("main", 1, file_path="src/pkg/cli.py")
    overload_stub = _document("locate", 10)
    real_definition = _document("locate", 20)
    real_definition_second_part = _document("locate", 60, part_number=2)
    documents = [
        _expanded(
            source,
            callees=[real_definition_second_part, real_definition, overload_stub],
            callers=[],
        )
    ]

    neighbors = select_neighbors([source["_id"]], documents)

    assert [neighbor.result.start_line for neighbor in neighbors] == [20]


def test_every_calling_part_is_kept_as_a_caller():
    source = _document("helper", 1)
    first_part = _document("long_function", 10)
    second_part = _document("long_function", 90, part_number=2)
    documents = [_expanded(source, callees=[], callers=[second_part, first_part])]

    neighbors = select_neighbors([source["_id"]], documents)

    assert [neighbor.result.start_line for neighbor in neighbors] == [10, 90]


def test_no_sources_found_means_no_neighbors():
    assert select_neighbors(["missing"], []) == []


def test_a_version_indexed_before_the_call_graph_is_refused():
    with pytest.raises(SearchRefusedError, match="index_repository.py again"):
        require_call_graph({"repository": "owner/repo"}, "owner/repo", "1.0")

    require_call_graph({"call_graph_edge_count": 0}, "owner/repo", "1.0")
