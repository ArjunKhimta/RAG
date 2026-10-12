"""Search every SWE-bench Lite subset task with its issue text and save how well each setup did.

Run from the repository root with the virtual environment active:

    PYTHONPATH=retrieval/src:eval/src python eval/scripts/run_swe_bench_retrieval.py

Reads `eval/questions/swe-bench-lite-subset.json` and the issue text from
`data/swe_bench/tasks.json` (refused unless it matches the SHA-256 the question file records).
Every task's base commit must be indexed, at that commit, under the commit ID as its version
(`index_repository.py`). Scores four
setups at the top 5 and the top 10 (see `evaluation.swe_bench_retrieval`) and writes
`eval/results/<time>-swe-bench-retrieval.json` and `.md`, then prints the table.

`--only <task ID>` (repeatable) scores just those tasks, for a check while the rest are still being
indexed; its files are named `-swe-bench-retrieval-partial` so they are never mistaken for the full
run. The results hold locations and ranks only, never issue text, patches, or code.

Makes no generation requests. Each task's issue costs one embedding request the first time it is
searched (the questions lane of the daily budget), and none afterwards. Exits 0 on success and 1 on
a refusal or failure. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_MODEL,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError
from retrieval.embedding_budget import build_query_embedder
from retrieval.redaction import redact
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, require_indexed_version
from retrieval.vector_search import require_searchable_version

from evaluation.results import (
    file_sha256,
    project_commit_state,
    relative_path,
    result_paths,
    source_location,
    write_json,
)
from evaluation.swe_bench_retrieval import (
    DEPTHS,
    DepthScore,
    SearchContext,
    SweBenchSetup,
    SweBenchTask,
    TaskFileError,
    load_tasks,
    render_table,
    run_search,
    score_results,
    summarize,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_QUESTIONS = PROJECT_ROOT / "eval" / "questions" / "swe-bench-lite-subset.json"

DEFAULT_TASKS = PROJECT_ROOT / "data" / "swe_bench" / "tasks.json"

DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "eval" / "results"

FULL_RUN_KIND = "swe-bench-retrieval"

PARTIAL_RUN_KIND = "swe-bench-retrieval-partial"


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    only = set(arguments.only) if arguments.only else None
    try:
        json_path, table_path, table = _evaluate(arguments, only)
    except (SearchRefusedError, TaskFileError) as error:
        _print(f"Refused: {error}")
        return 1
    except (MissingConfigError, PyMongoError, EmbeddingRequestError, OSError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    for line in table.splitlines():
        _print(line)
    _print("")
    _print(f"Saved {_relative(json_path)} and {_relative(table_path)}")
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--only", action="append", help="score only this task ID (repeatable)")
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    return parser.parse_args()


def _evaluate(arguments: argparse.Namespace, only: set[str] | None) -> tuple[Path, Path, str]:
    run_at = datetime.now(UTC)
    question_document, tasks = load_tasks(arguments.questions, arguments.tasks, only)
    database = build_mongo_client()[MONGODB_DATABASE]
    embedder = build_query_embedder(database, build_gemini_client())
    chunks_collection = database[CHUNKS_COLLECTION]
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    records = {task.task_id: _require_indexed(database, task, embedder) for task in tasks}
    context = SearchContext(chunks_collection=chunks_collection, embedder=embedder)
    scores: dict[tuple[str, int], list[DepthScore]] = {}
    locations: dict[str, dict[str, Any]] = {task.task_id: {} for task in tasks}
    for setup in SweBenchSetup:
        for depth in DEPTHS:
            key = (str(setup), depth)
            scores[key] = []
            for task in tasks:
                results, milliseconds = run_search(context, setup, task, depth)
                scores[key].append(score_results(task, results, depth, milliseconds))
                locations[task.task_id][f"{setup} top {depth}"] = [
                    source_location(result) for result in results
                ]
    summaries = [summarize(setup, depth, tasks, scores[(setup, depth)]) for setup, depth in scores]
    details = _run_details(run_at, arguments, question_document, embedder.miss_count, only)
    table = render_table(_detail_lines(details), summaries, tasks, scores)
    kind = FULL_RUN_KIND if only is None else PARTIAL_RUN_KIND
    json_path, table_path = result_paths(arguments.output_directory, run_at, kind)
    write_json(
        json_path,
        {
            "run": details,
            "summaries": [
                {**summary.__dict__, "definition_recall": summary.definition_recall}
                for summary in summaries
            ],
            "tasks": [
                _task_record(task, records[task.task_id], scores, locations[task.task_id])
                for task in tasks
            ],
        },
    )
    table_path.write_text(table, encoding="utf-8")
    return json_path, table_path, table


def _require_indexed(database: Any, task: SweBenchTask, embedder: Any) -> dict[str, Any]:
    record = MongoChunkStore(database).find_repository_record(task.repository, task.base_commit)
    require_indexed_version(record, task.repository, task.base_commit)
    require_searchable_version(record, task.repository, task.base_commit, embedder)
    if record["commit_id"] != task.base_commit:
        raise SearchRefusedError(
            f"{task.task_id} is indexed at {record['commit_id'][:12]}, not its base commit"
        )
    return record


def _run_details(
    run_at: datetime,
    arguments: argparse.Namespace,
    question_document: dict[str, Any],
    embedding_requests: int,
    only: set[str] | None,
) -> dict[str, Any]:
    project_commit, has_changes = project_commit_state(PROJECT_ROOT)
    return {
        "run_at": run_at.isoformat(timespec="seconds"),
        "project_commit": project_commit,
        "project_has_uncommitted_changes": has_changes,
        "question_file": relative_path(arguments.questions, PROJECT_ROOT),
        "question_file_sha256": file_sha256(arguments.questions),
        "dataset": question_document["dataset"],
        "dataset_revision": question_document["revision"],
        "tasks_file_sha256": question_document["tasks_file_sha256"],
        "only_tasks": sorted(only) if only is not None else None,
        "embedding_model": GEMINI_EMBEDDING_MODEL,
        "embedding_dimensions": EMBEDDING_DIMENSIONS,
        "embedding_requests": embedding_requests,
    }


def _detail_lines(details: dict[str, Any]) -> list[str]:
    changes = " (uncommitted changes)" if details["project_has_uncommitted_changes"] else ""
    lines = [
        f"- Run at {details['run_at']}, project commit `{details['project_commit'][:12]}`{changes}",
        f"- Tasks: `{details['question_file']}` (SHA-256 `{details['question_file_sha256'][:12]}`)"
        f", from {details['dataset']} at revision `{details['dataset_revision'][:12]}`",
        f"- Embedding: {details['embedding_model']} at {details['embedding_dimensions']} "
        f"dimensions; embedding requests made by this run: {details['embedding_requests']}",
    ]
    if details["only_tasks"] is not None:
        lines.append(f"- Partial run, only: {', '.join(details['only_tasks'])}")
    return lines


def _task_record(
    task: SweBenchTask,
    record: dict[str, Any],
    scores: dict[tuple[str, int], list[DepthScore]],
    locations: dict[str, Any],
) -> dict[str, Any]:
    by_setup: dict[str, Any] = {}
    for (setup, depth), task_scores in scores.items():
        score = next(item for item in task_scores if item.task_id == task.task_id)
        by_setup[f"{setup} top {depth}"] = {
            "file_rank": score.file_rank,
            "definition_ranks": list(score.definition_ranks),
            "milliseconds": round(score.milliseconds, 1),
            "results": locations[f"{setup} top {depth}"],
        }
    return {
        "id": task.task_id,
        "repository": task.repository,
        "base_commit": task.base_commit,
        "indexed_commit": record["commit_id"],
        "patch_files": list(task.patch_files),
        "expected": [expected.qualified_name for expected in task.expected],
        "setups": by_setup,
    }


def _relative(path: Path) -> str:
    return relative_path(path, PROJECT_ROOT)


def _print(text: str) -> None:
    print(redact(text))


if __name__ == "__main__":
    sys.exit(main())
