"""Prepare and score the hand check of a faithfulness run (see `evaluation.hand_check`).

Run from the repository root with the virtual environment active:

    PYTHONPATH=eval/src python eval/scripts/hand_check_faithfulness.py prepare \
        eval/results/<time>-faithfulness.json
    PYTHONPATH=eval/src python eval/scripts/hand_check_faithfulness.py score \
        eval/results/<time>-handcheck-labels.json

`prepare` reads the judge's verdicts and the answer run they judged (checked against its saved
SHA-256), fetches every source from Atlas by chunk ID, picks about 40 sentences, and writes:
- `data/hand_checks/<time>-worksheet.md`: each item's answer, the sentence to label, its cited
  lines, and links to every source; it holds code, so it stays in the gitignored data folder
- `eval/results/<time>-handcheck-labels.json`: one blank label per item, to fill in
- `eval/results/<time>-handcheck-key.json`: the verdicts, reasons, groups, and setups; do not
  open it until the labels are done, or the check is no longer blind

`score` reads a finished labels file and its key and writes `eval/results/<time>-handcheck.md`.
It refuses a labels file with any item left blank or labelled with an unknown verdict.

Makes no Gemini requests. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError
from retrieval.clients import build_mongo_client
from retrieval.config import MONGODB_DATABASE, MissingConfigError, load_environment
from retrieval.redaction import redact
from retrieval.search_results import SearchRefusedError

from evaluation.faithfulness_inputs import (
    AnswerRunFormatError,
    ChangedSourceError,
    answers_to_judge,
    fetch_answer_sources,
)
from evaluation.faithfulness_metrics import JudgingOutcome
from evaluation.hand_check import (
    HAND_CHECK_SEED,
    LabelsError,
    judged_sentences,
    key_document,
    labels_document,
    render_worksheet,
    score_labels,
    select_items,
)
from evaluation.hand_check_results import render_hand_check_table
from evaluation.results import RESULT_TIME_FORMAT, file_sha256, relative_path, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESULTS_DIRECTORY = PROJECT_ROOT / "eval" / "results"

WORKSHEET_DIRECTORY = PROJECT_ROOT / "data" / "hand_checks"

LABELS_SUFFIX = "-handcheck-labels.json"

KEY_SUFFIX = "-handcheck-key.json"

TABLE_SUFFIX = "-handcheck.md"


class _MismatchedFilesError(ValueError):
    """Raised when a file does not match the run it says it came from."""


def main() -> int:
    arguments = _parse_arguments()
    try:
        if arguments.command == "prepare":
            _prepare(arguments.faithfulness)
        else:
            _score(arguments.labels)
    except (
        AnswerRunFormatError,
        ChangedSourceError,
        LabelsError,
        SearchRefusedError,
        _MismatchedFilesError,
    ) as error:
        _print(f"Refused: {error}")
        return 1
    except (MissingConfigError, PyMongoError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    return 0


def _prepare(faithfulness_path: Path) -> None:
    load_environment()
    started_at = datetime.now(UTC)
    faithfulness_run = _read_json(faithfulness_path)
    answers_path = PROJECT_ROOT / faithfulness_run["run"]["answers_file"]
    if file_sha256(answers_path) != faithfulness_run["run"]["answers_file_sha256"]:
        raise _MismatchedFilesError(
            f"{_relative(answers_path)} has changed since the faithfulness run judged it"
        )
    answer_run = _read_json(answers_path)
    answers = answers_to_judge(answer_run)
    database = build_mongo_client()[MONGODB_DATABASE]
    sources = fetch_answer_sources(database, answer_run["run"], answers)
    sources_by_answer = {
        (answer.question_id, answer.setup): answer_sources
        for answer, answer_sources in zip(answers, sources, strict=True)
    }
    sentences_by_answer = {
        (answer.question_id, answer.setup): answer.sentences for answer in answers
    }
    sentences = [
        sentence
        for record in faithfulness_run["answers"]
        if record["outcome"] == JudgingOutcome.JUDGED
        for sentence in judged_sentences(
            record["id"],
            record["setup"],
            record["verdicts"],
            sentences_by_answer[(record["id"], record["setup"])],
            sources_by_answer[(record["id"], record["setup"])],
        )
    ]
    items = select_items(sentences, HAND_CHECK_SEED)
    stem = started_at.strftime(RESULT_TIME_FORMAT)
    worksheet_path = WORKSHEET_DIRECTORY / f"{stem}-worksheet.md"
    labels_path = RESULTS_DIRECTORY / f"{stem}{LABELS_SUFFIX}"
    key_path = RESULTS_DIRECTORY / f"{stem}{KEY_SUFFIX}"
    run_details = faithfulness_run["run"]
    worksheet = render_worksheet(
        items,
        sentences_by_answer,
        sources_by_answer,
        run_details["repository"],
        run_details["indexed_commit"],
    )
    source_files = {
        "faithfulness_file": _relative(faithfulness_path),
        "faithfulness_file_sha256": file_sha256(faithfulness_path),
    }
    worksheet_path.parent.mkdir(parents=True, exist_ok=True)
    worksheet_path.write_text(worksheet, encoding="utf-8")
    write_json(labels_path, {**source_files, **labels_document(items, _relative(worksheet_path))})
    write_json(key_path, {**source_files, **key_document(items, sentences, HAND_CHECK_SEED)})
    _print(f"Judged sentences: {len(sentences)}; items to label: {len(items)}")
    _print(f"Worksheet: {_relative(worksheet_path)}")
    _print(f"Fill in:   {_relative(labels_path)}")
    _print(f"Key:       {_relative(key_path)} (do not open until every label is filled in)")


def _score(labels_path: Path) -> None:
    if not labels_path.name.endswith(LABELS_SUFFIX):
        raise _MismatchedFilesError(f"A labels file name ends with {LABELS_SUFFIX}")
    stem = labels_path.name.removesuffix(LABELS_SUFFIX)
    key_path = labels_path.with_name(f"{stem}{KEY_SUFFIX}")
    labels = _read_json(labels_path)
    key = _read_json(key_path)
    if labels["faithfulness_file_sha256"] != key["faithfulness_file_sha256"]:
        raise _MismatchedFilesError("The labels file and its key come from different runs")
    score = score_labels(labels, key)
    table = render_hand_check_table(score, key, _relative(labels_path), labels["faithfulness_file"])
    table_path = labels_path.with_name(f"{stem}{TABLE_SUFFIX}")
    table_path.write_text(table, encoding="utf-8")
    for line in table.splitlines():
        _print(line)
    _print("")
    _print(f"Saved {_relative(table_path)}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path) -> str:
    return relative_path(path, PROJECT_ROOT)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="pick the sentences and write the worksheet")
    prepare.add_argument("faithfulness", type=Path, help="a faithfulness JSON in eval/results/")
    score = commands.add_parser("score", help="compare finished labels with the judge")
    score.add_argument("labels", type=Path, help="a filled-in handcheck labels JSON")
    return parser.parse_args()


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
