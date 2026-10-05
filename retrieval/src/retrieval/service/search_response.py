"""The JSON reply of `POST /search`, built from a finished search.

Each result gives its location, a link to its lines at the indexed commit, and a snippet of its
opening lines, at most 12 with their real line numbers (a class outline's without numbers), so no
reply carries a whole file. The score is vector search's (1 + cosine) / 2: it orders the results
of one search and is not a confidence. The repository block is the same as in `POST /ask`, with
the license and a link to the license file at the indexed commit.
"""

from __future__ import annotations

from typing import Any

from retrieval.asking import SearchRun
from retrieval.search_results import SearchResult
from retrieval.service.ask_response import repository_block, rounded_timings, snippet_fields
from retrieval.snippets import github_line_link, opening_snippet


def search_response(search_run: SearchRun, repository: str, version: str) -> dict[str, Any]:
    commit_id = search_run.repository_record["commit_id"]
    return {
        "results": [
            _result(rank, result, repository, commit_id)
            for rank, result in enumerate(search_run.results, start=1)
        ],
        "repository": repository_block(search_run.repository_record, repository, version),
        "usage": {"embedding": str(search_run.embedding_use)},
        "timings_ms": rounded_timings(search_run.timings),
    }


def _result(rank: int, result: SearchResult, repository: str, commit_id: str) -> dict[str, Any]:
    return {
        "rank": rank,
        "file_path": result.file_path,
        "kind": result.kind,
        "qualified_name": result.qualified_name,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "is_test_file": result.is_test_file,
        "score": result.score,
        "link": github_line_link(
            repository, commit_id, result.file_path, result.start_line, result.end_line
        ),
        "snippet": snippet_fields(opening_snippet(result)),
    }
