"""Find the callers and callees of the chunks chosen as answer sources, using the call graph.

One aggregation does it. It matches the source chunks by ID, then runs two `$graphLookup` stages
on the same collection: callees follow each source's `calls` to chunks whose `symbol` matches,
and callers find chunks whose `calls` contain the source's `symbol`. `maxDepth: 0` keeps it to
one hop, direct callers and callees only; a second hop would multiply the neighbors and swamp a
small prompt. Both lookups stay inside the source's repository version and skip test files,
because tests call almost everything.

The neighbors are then picked in Python:
- A callee is shown by its first part, which holds its header. When a name is defined more than
  once in a file, such as `typing.overload` stubs or a property and its setter, the last
  definition is chosen, because that is the one Python uses when the code runs.
- A caller is shown by the part that contains the call, because each part lists its own calls.
- Chunks already among the sources, and repeats, are dropped. Each neighbor keeps its first
  relation, going through sources in rank order, callees before callers.

Neighbors come back unscored; reranking them against the question is a separate step.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pymongo.collection import Collection

from retrieval.chunk_store import CHUNKS_COLLECTION
from retrieval.search_results import SearchRefusedError, SearchResult, search_result_from

UNSCORED = 0.0

CALLEES_FIELD = "callees"

CALLERS_FIELD = "callers"

EDGE_COUNT_FIELD = "call_graph_edge_count"


class NeighborRelation(StrEnum):
    CALLEE = "called by"
    CALLER = "calls"


@dataclass(frozen=True)
class GraphNeighbor:
    """A chunk joined to a source by one call. `relation` reads from the neighbor's side.

    A callee is "called by" its source, and a caller "calls" its source.
    """

    result: SearchResult
    relation: NeighborRelation
    source_chunk_id: str


def require_call_graph(repository_record: dict[str, Any], repository: str, version: str) -> None:
    if EDGE_COUNT_FIELD not in repository_record:
        raise SearchRefusedError(
            f"{repository} at {version} was indexed before the call graph existed; "
            "run index_repository.py again to add it"
        )


def find_graph_neighbors(
    chunks_collection: Collection,
    sources: list[SearchResult],
    repository: str,
    version: str,
) -> list[GraphNeighbor]:
    """Return the direct callers and callees of `sources`, which must be in rank order."""
    source_ids = [source.chunk_id for source in sources]
    if not source_ids:
        return []
    pipeline = build_expansion_pipeline(source_ids, repository, version)
    documents = list(chunks_collection.aggregate(pipeline))
    return select_neighbors(source_ids, documents)


def build_expansion_pipeline(
    source_ids: list[str], repository: str, version: str
) -> list[dict[str, Any]]:
    neighbor_filter = {"repository": repository, "version": version, "is_test_file": False}
    return [
        {"$match": {"_id": {"$in": source_ids}}},
        {
            "$graphLookup": {
                "from": CHUNKS_COLLECTION,
                "startWith": "$calls",
                "connectFromField": "calls",
                "connectToField": "symbol",
                "as": CALLEES_FIELD,
                "maxDepth": 0,
                "restrictSearchWithMatch": neighbor_filter,
            }
        },
        {
            "$graphLookup": {
                "from": CHUNKS_COLLECTION,
                "startWith": "$symbol",
                "connectFromField": "symbol",
                "connectToField": "calls",
                "as": CALLERS_FIELD,
                "maxDepth": 0,
                "restrictSearchWithMatch": neighbor_filter,
            }
        },
        {"$project": {"_id": 1, CALLEES_FIELD: 1, CALLERS_FIELD: 1}},
        {"$project": {f"{CALLEES_FIELD}.embedding": 0, f"{CALLERS_FIELD}.embedding": 0}},
    ]


def select_neighbors(
    source_ids: list[str], documents: list[dict[str, Any]]
) -> list[GraphNeighbor]:
    documents_by_source = {document["_id"]: document for document in documents}
    seen_ids = set(source_ids)
    neighbors: list[GraphNeighbor] = []
    for source_id in source_ids:
        document = documents_by_source.get(source_id)
        if document is None:
            continue
        related = [
            *[
                (callee, NeighborRelation.CALLEE)
                for callee in _running_definitions(document[CALLEES_FIELD])
            ],
            *[
                (caller, NeighborRelation.CALLER)
                for caller in sorted(document[CALLERS_FIELD], key=_location_of)
            ],
        ]
        for neighbor_document, relation in related:
            if neighbor_document["_id"] in seen_ids:
                continue
            seen_ids.add(neighbor_document["_id"])
            neighbors.append(
                GraphNeighbor(
                    result=search_result_from({**neighbor_document, "score": UNSCORED}),
                    relation=relation,
                    source_chunk_id=source_id,
                )
            )
    return neighbors


def _running_definitions(callee_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one chunk per symbol: the first part of its last definition in the file."""
    chosen_by_symbol: dict[str, dict[str, Any]] = {}
    for document in callee_documents:
        if document.get("part_number", 1) != 1:
            continue
        symbol = document["symbol"]
        chosen = chosen_by_symbol.get(symbol)
        if chosen is None or document["start_line"] > chosen["start_line"]:
            chosen_by_symbol[symbol] = document
    return sorted(chosen_by_symbol.values(), key=_location_of)


def _location_of(document: dict[str, Any]) -> tuple[str, int]:
    return document["file_path"], document["start_line"]
