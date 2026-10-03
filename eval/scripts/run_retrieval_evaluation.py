"""Run every question through every search setup and save the retrieval evaluation.

Run from the repository root with the virtual environment active. The evaluation package is not
installed, so its folder is put on the path for the command:

    PYTHONPATH=eval/src python eval/scripts/run_retrieval_evaluation.py \
        eval/questions/pallets-flask-3.1.3.json

Compares eight setups (see `evaluation.search_setups`) on every question whose answer is in the
code, scoring recall, complete questions, mean reciprocal rank, and time (see
`evaluation.retrieval_metrics`). Writes `eval/results/<time>-retrieval.json` and `.md` and prints
the table. Makes no generation requests; question embeddings come from the cache, and a question
never embedded before costs one embedding request.

Refuses to run when the question file was written for a different commit than the one indexed,
when the version was indexed before the call graph, or when a search index is not ready. Exits 0
on success and 1 on a refusal or failure. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError
from retrieval.config import MissingConfigError, load_environment
from retrieval.embedders import EmbeddingRequestError
from retrieval.question_set import EvaluationQuestion
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError
from retrieval.search_results import SearchRefusedError

from evaluation.preparation import prepare_run
from evaluation.results import (
    build_run_details,
    details_as_dict,
    group_summaries,
    question_groups,
    relative_path,
    render_retrieval_table,
    result_paths,
    source_location,
    summary_as_dict,
    write_json,
)
from evaluation.retrieval_metrics import QuestionScore, score_question
from evaluation.search_setups import SearchSetup, SetupRun, run_all_setups

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "eval" / "results"


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        json_path, table_path, table = _evaluate(arguments.questions, arguments.output_directory)
    except SearchRefusedError as error:
        _print(f"Refused: {error}")
        return 1
    except (MissingConfigError, PyMongoError, RerankerModelError, EmbeddingRequestError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    for line in table.splitlines():
        _print(line)
    _print("")
    _print(f"Saved {_relative(json_path)} and {_relative(table_path)}")
    return 0


def _evaluate(question_path: Path, output_directory: Path) -> tuple[Path, Path, str]:
    run_at = datetime.now(UTC)
    prepared = prepare_run(question_path)
    question_set = prepared.question_set
    answerable = [question for question in question_set.questions if question.answer_in_code]
    skipped_ids = [
        question.question_id for question in question_set.questions if not question.answer_in_code
    ]
    runs_by_question = {
        question.question_id: run_all_setups(prepared.context, question.question)
        for question in answerable
    }
    scores_by_setup = _scores_by_setup(answerable, runs_by_question)
    summaries = group_summaries(question_groups(answerable), scores_by_setup)
    details = build_run_details(
        run_at, question_path, question_set, prepared.repository_record, PROJECT_ROOT
    )
    sources_by_setup = {
        setup: max(len(runs[setup].sources) for runs in runs_by_question.values())
        for setup in scores_by_setup
    }
    embedding_requests = prepared.embedder.miss_count
    table = render_retrieval_table(
        details, summaries, sources_by_setup, skipped_ids, embedding_requests
    )
    json_path, table_path = result_paths(output_directory, run_at)
    write_json(
        json_path,
        {
            "run": details_as_dict(details),
            "embedding_requests": embedding_requests,
            "skipped_question_ids": skipped_ids,
            "summaries": {
                label: {setup: summary_as_dict(summary) for setup, summary in by_setup.items()}
                for label, by_setup in summaries.items()
            },
            "questions": [
                _question_record(question, runs_by_question[question.question_id], scores_by_setup)
                for question in answerable
            ],
        },
    )
    table_path.write_text(table, encoding="utf-8")
    return json_path, table_path, table


def _scores_by_setup(
    questions: list[EvaluationQuestion],
    runs_by_question: dict[str, dict[SearchSetup, SetupRun]],
) -> dict[str, list[QuestionScore]]:
    scores_by_setup: dict[str, list[QuestionScore]] = {str(setup): [] for setup in SearchSetup}
    for question in questions:
        for setup, run in runs_by_question[question.question_id].items():
            scores_by_setup[str(setup)].append(
                score_question(question, run.sources, run.milliseconds)
            )
    return scores_by_setup


def _question_record(
    question: EvaluationQuestion,
    runs: dict[SearchSetup, SetupRun],
    scores_by_setup: dict[str, list[QuestionScore]],
) -> dict[str, Any]:
    setups: dict[str, Any] = {}
    for setup, run in runs.items():
        score = next(
            score
            for score in scores_by_setup[str(setup)]
            if score.question_id == question.question_id
        )
        setups[str(setup)] = {
            "found_count": score.found_count,
            "expected_count": score.expected_count,
            "first_hit_rank": score.first_hit_rank,
            "milliseconds": round(run.milliseconds, 1),
            "sources": [source_location(source) for source in run.sources],
        }
    return {
        "id": question.question_id,
        "question": question.question,
        "origin": question.origin,
        "query_style": question.query_style,
        "expected": [expected.qualified_name for expected in question.expected],
        "setups": setups,
    }


def _relative(path: Path) -> str:
    return relative_path(path, PROJECT_ROOT)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("questions", type=Path, help="a question file in eval/questions/")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="where to save the results (default eval/results/)",
    )
    return parser.parse_args()


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
