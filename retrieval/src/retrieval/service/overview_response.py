"""The JSON reply of `POST /overview`: every source file with its classes, functions, and calls,
the file-to-file links with how many calls each carries, totals, what was left out (folders, and
how many files and calls), and the repository block with the license link. Locations and counts
only, never code."""

from __future__ import annotations

from typing import Any

from retrieval.repository_overview import FileLink, FileOverview, OverviewRun
from retrieval.service.ask_response import repository_block, rounded_timings
from retrieval.snippets import github_file_link


def overview_response(run: OverviewRun, repository: str, version: str) -> dict[str, Any]:
    overview = run.overview
    commit_id = run.repository_record["commit_id"]
    return {
        "repository": repository_block(run.repository_record, repository, version),
        "files": [_file(file, repository, commit_id) for file in overview.files],
        "links": [_link(link) for link in overview.links],
        "totals": {
            "files": len(overview.files),
            "classes": sum(file.class_count for file in overview.files),
            "functions": sum(file.function_count for file in overview.files),
            "calls": overview.call_count,
            "calls_between_files": overview.calls_between_files,
            "calls_within_files": overview.calls_within_files,
        },
        "left_out": {
            "folders": [f"{folder}/" for folder in overview.left_out_folders],
            "files": overview.left_out_file_count,
            "calls": overview.left_out_call_count,
        },
        "timings_ms": rounded_timings(run.timings),
    }


def _file(file: FileOverview, repository: str, commit_id: str) -> dict[str, Any]:
    return {
        "path": file.path,
        "classes": file.class_count,
        "functions": file.function_count,
        "calls_within": file.calls_within,
        "calls_out": file.calls_out,
        "calls_in": file.calls_in,
        "link": github_file_link(repository, commit_id, file.path),
    }


def _link(link: FileLink) -> dict[str, Any]:
    return {"from": link.caller_file, "to": link.callee_file, "calls": link.call_count}
