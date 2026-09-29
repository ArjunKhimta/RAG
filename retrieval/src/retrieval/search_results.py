"""What every search returns, and the check every search makes first.

Vector search and keyword search both return `SearchResult` records, best first, so rank fusion
can combine the two lists. Their scores cannot be compared: a vector score is (1 + cosine) / 2,
between 0 and 1, while a BM25 score is unbounded and depends on the query. Fusion must go by rank.

Searching a version with no `repositories` record is refused: that record is written only once
indexing has fully finished, so without it the results could silently miss chunks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from retrieval.config import MAX_SEARCH_LIMIT

RESULT_FIELDS = (
    "file_path",
    "start_line",
    "end_line",
    "kind",
    "qualified_name",
    "signature",
    "part_number",
    "part_count",
    "is_test_file",
    "text",
)


class SearchRefusedError(RuntimeError):
    """Raised when searching would give missing or meaningless results."""


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    kind: str
    qualified_name: str
    signature: str | None
    part_number: int
    part_count: int
    is_test_file: bool
    text: str
    score: float


def validate_search_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ValueError(f"The search limit must be between 1 and {MAX_SEARCH_LIMIT}")


def result_projection(score_source: str) -> dict[str, Any]:
    """Project the fields a result needs, with the score from the given search metadata."""
    projected_fields: dict[str, Any] = {field_name: 1 for field_name in RESULT_FIELDS}
    projected_fields["score"] = {"$meta": score_source}
    return projected_fields


def search_result_from(document: dict[str, Any]) -> SearchResult:
    return SearchResult(
        chunk_id=document["_id"],
        file_path=document["file_path"],
        start_line=document["start_line"],
        end_line=document["end_line"],
        kind=document["kind"],
        qualified_name=document["qualified_name"],
        signature=document.get("signature"),
        part_number=document.get("part_number", 1),
        part_count=document.get("part_count", 1),
        is_test_file=bool(document.get("is_test_file", False)),
        text=document["text"],
        score=float(document["score"]),
    )


def require_indexed_version(
    repository_record: dict[str, Any] | None, repository: str, version: str
) -> None:
    if repository_record is None:
        raise SearchRefusedError(
            f"{repository} at {version} has not finished indexing; "
            "run index_repository.py until it completes"
        )
