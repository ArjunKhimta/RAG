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
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_MODEL,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    RERANKER_MODEL_REPOSITORY,
    RERANKER_MODEL_REVISION,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import EmbeddingRequestError, GeminiQueryEmbedder
from retrieval.graph_expansion import require_call_graph
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.question_set import EvaluationQuestion, QuestionSet, load_question_set
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError, verified_reranker_files
from retrieval.reranking import CrossEncoderScorer
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, require_indexed_version
from retrieval.vector_search import require_searchable_version

from evaluation.results import (
    RunDetails,
    details_as_dict,
    file_sha256,
    group_summaries,
    project_commit_state,
    question_groups,
    render_retrieval_table,
    result_paths,
    source_location,
    summary_as_dict,
    write_json,
)
from evaluation.retrieval_metrics import QuestionScore, score_question
from evaluation.search_setups import SearchContext, SearchSetup, SetupRun, run_all_setups

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
    question_set = load_question_set(question_path)
    database = build_mongo_client()[MONGODB_DATABASE]
    record = MongoChunkStore(database).find_repository_record(
        question_set.repository, question_set.version
    )
    require_indexed_version(record, question_set.repository, question_set.version)
    require_call_graph(record, question_set.repository, question_set.version)
    if record["commit_id"] != question_set.commit_id:
        raise SearchRefusedError(
            f"The questions were written for commit {question_set.commit_id}, but the index is at "
            f"{record['commit_id']}"
        )
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    embedder = CachingQueryEmbedder(
        GeminiQueryEmbedder(build_gemini_client()), query_embedding_store
    )
    require_searchable_version(record, question_set.repository, question_set.version, embedder)
    chunks_collection = database[CHUNKS_COLLECTION]
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    context = SearchContext(
        chunks_collection=chunks_collection,
        embedder=embedder,
        scorer=CrossEncoderScorer(verified_reranker_files()),
        repository=question_set.repository,
        version=question_set.version,
    )
    answerable = [question for question in question_set.questions if question.answer_in_code]
    skipped_ids = [
        question.question_id for question in question_set.questions if not question.answer_in_code
    ]
    runs_by_question = {
        question.question_id: run_all_setups(context, question.question) for question in answerable
    }
    scores_by_setup = _scores_by_setup(answerable, runs_by_question)
    summaries = group_summaries(question_groups(answerable), scores_by_setup)
    details = _run_details(run_at, question_path, question_set, record)
    sources_by_setup = {
        setup: max(len(runs[setup].sources) for runs in runs_by_question.values())
        for setup in scores_by_setup
    }
    table = render_retrieval_table(
        details, summaries, sources_by_setup, skipped_ids, embedder.miss_count
    )
    json_path, table_path = result_paths(output_directory, run_at)
    write_json(
        json_path,
        {
            "run": details_as_dict(details),
            "embedding_requests": embedder.miss_count,
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


def _run_details(
    run_at: datetime, question_path: Path, question_set: QuestionSet, record: dict[str, Any]
) -> RunDetails:
    commit, has_changes = project_commit_state(PROJECT_ROOT)
    return RunDetails(
        run_at=run_at.isoformat(timespec="seconds"),
        project_commit=commit,
        project_has_uncommitted_changes=has_changes,
        question_file=_relative(question_path),
        question_file_sha256=file_sha256(question_path),
        repository=question_set.repository,
        version=question_set.version,
        indexed_commit=record["commit_id"],
        embedding_model=GEMINI_EMBEDDING_MODEL,
        embedding_dimensions=EMBEDDING_DIMENSIONS,
        reranker_model=RERANKER_MODEL_REPOSITORY,
        reranker_revision=RERANKER_MODEL_REVISION,
    )


def _relative(path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(PROJECT_ROOT):
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    return str(path)


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
