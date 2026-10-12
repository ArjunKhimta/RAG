"""Write the expected definitions of the SWE-bench Lite subset (see `evaluation.swe_bench`).

Run from the repository root with the virtual environment active:

    PYTHONPATH=retrieval/src:eval/src python eval/scripts/prepare_swe_bench_questions.py

Reads `data/swe_bench/tasks.json` (the chosen rows of princeton-nlp/SWE-bench_Lite, fetched once)
and the clones with full history in `data/swe_bench/git/<owner>__<repo>/`, reads each patched file
at its task's base commit with `git show`, and writes `eval/questions/swe-bench-lite-subset.json`.

That file holds task IDs, base commits, the files each fix changed, and the expected definitions.
It never holds the issue text or the patch: the dataset states no license, and its issues are
people's own writing. The evaluation reads them from `data/swe_bench/tasks.json` at run time,
checked against the SHA-256 recorded here.

Git runs without personal or system settings, hooks, or prompts, and only reads. Nothing from the
repositories is run. Makes no Gemini or database requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.swe_bench import (
    ExpectedChange,
    PatchFormatError,
    expected_changes,
    read_file_changes,
    split_module_level,
)

DEFAULT_TASKS_FILE = Path("data/swe_bench/tasks.json")

DEFAULT_GIT_FOLDER = Path("data/swe_bench/git")

DEFAULT_OUTPUT_FILE = Path("eval/questions/swe-bench-lite-subset.json")

GIT_TIMEOUT_SECONDS = 60

SAFE_GIT_SETTINGS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]

NOTES = (
    "A subset of SWE-bench Lite for scoring search: each task's issue is the query, and the "
    "expected definitions are the chunks at the base commit holding the lines the merged fix "
    "removes or rewrites, or, for lines it only adds, the line just before them (the line after "
    "when that line belongs to no definition). Each line goes to the smallest chunk holding it; "
    "a split definition is expected whole. Module-level code the fix also changed (import lines) "
    "is listed under also_changed_at_module_level and not expected, unless it is all the fix "
    "changed. Line numbers are 1-indexed and inclusive. The issue text and the patch are not "
    "stored here, since the dataset states no license; they are read from the tasks file named "
    "below, checked by its SHA-256."
)


class PreparationError(RuntimeError):
    """Raised when the tasks file or a clone cannot be read as expected."""


def main() -> int:
    arguments = parse_arguments()
    try:
        document = prepare(arguments.tasks, arguments.git_folder)
    except (PreparationError, PatchFormatError, OSError, json.JSONDecodeError) as error:
        print(f"Could not prepare the SWE-bench questions: {error}", file=sys.stderr)
        return 1
    arguments.output.write_text(json.dumps(document, indent=2) + "\n")
    print(f"Wrote {arguments.output}")
    for task in document["tasks"]:
        expected = ", ".join(
            f"{item['qualified_name']} ({item['start_line']}-{item['end_line']})"
            for item in task["expected"]
        )
        print(f"  {task['id']}: {expected}")
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_FILE)
    parser.add_argument("--git-folder", type=Path, default=DEFAULT_GIT_FOLDER)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE)
    return parser.parse_args()


def prepare(tasks_file: Path, git_folder: Path) -> dict[str, Any]:
    tasks_bytes = tasks_file.read_bytes()
    tasks_document = json.loads(tasks_bytes)
    environment = isolated_git_environment()
    return {
        "dataset": tasks_document["dataset"],
        "split": tasks_document["split"],
        "revision": tasks_document["revision"],
        "tasks_file": str(tasks_file),
        "tasks_file_sha256": hashlib.sha256(tasks_bytes).hexdigest(),
        "written_on": datetime.now(UTC).date().isoformat(),
        "notes": NOTES,
        "tasks": [prepare_task(task, git_folder, environment) for task in tasks_document["tasks"]],
    }


def prepare_task(
    task: dict[str, Any], git_folder: Path, environment: dict[str, str]
) -> dict[str, Any]:
    clone = git_folder / task["repo"].replace("/", "__")
    if not clone.is_dir():
        raise PreparationError(f"no clone of {task['repo']} in {git_folder}")
    expected: list[ExpectedChange] = []
    module_level: list[ExpectedChange] = []
    changes = read_file_changes(task["patch"])
    for change in changes:
        source = read_file_at(clone, task["base_commit"], change.file_path, environment)
        definitions, imports = split_module_level(expected_changes(change, source))
        expected.extend(definitions)
        module_level.extend(imports)
    if not expected:
        raise PreparationError(f"{task['instance_id']} changes no code that existed before")
    return {
        "id": task["instance_id"],
        "repository": task["repo"],
        "base_commit": task["base_commit"],
        "dataset_version": task["version"],
        "issue_created_at": task["created_at"],
        "patch_files": [change.file_path for change in changes],
        "expected": [expected_record(item) for item in expected],
        "also_changed_at_module_level": [expected_record(item) for item in module_level],
    }


def expected_record(change: ExpectedChange) -> dict[str, Any]:
    return {
        "file_path": change.file_path,
        "qualified_name": change.qualified_name,
        "kind": change.kind,
        "start_line": change.start_line,
        "end_line": change.end_line,
        "reasons": list(change.reasons),
        "lines": list(change.lines),
    }


def read_file_at(clone: Path, commit: str, file_path: str, environment: dict[str, str]) -> bytes:
    completed = subprocess.run(
        ["git", *SAFE_GIT_SETTINGS, "show", f"{commit}:{file_path}"],
        cwd=clone,
        env=environment,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise PreparationError(f"git could not read {file_path} at {commit[:12]}")
    return completed.stdout


def isolated_git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": tempfile.mkdtemp(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }


if __name__ == "__main__":
    sys.exit(main())
