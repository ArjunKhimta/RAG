"""Judge the faithfulness of a saved answer run, one verdict per cited sentence.

Run from the repository root with the virtual environment active. The evaluation package is not
installed, so its folder is put on the path for the command:

    PYTHONPATH=eval/src python eval/scripts/run_faithfulness_evaluation.py \
        eval/results/<time>-answers.json

Reads every answered reply in the answers file, fetches its sources from Atlas by chunk ID, and
asks the judge (see `evaluation.faithfulness_judge`) whether each cited sentence is supported by
its cited lines, miscited, or unsupported by any source. Every source is fetched and checked
against the saved run before the first request, so a changed index stops the run before any
quota is used. Writes `eval/results/<time>-faithfulness.json` and `.md` and prints the table.
The JSON keeps each sentence's text, verdict, and the judge's reason for the hand check, and
never any code.

Only answer runs saved with per-sentence citations and source chunk IDs can be judged; older
runs are refused. `--max-answers` judges only the first answered replies, for a cheap check
before a full run.

Costs 1 generation request per answered reply (about 85 for a 50-question run of two setups;
more if a temporary failure is retried), paced under the per-minute limit, and no embedding
requests. A used-up daily quota stops the run: the replies judged so far are saved, marked
incomplete, and the exit code is 1. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo.errors import PyMongoError
from retrieval.answer_generation import AnswerModel, GeminiAnswerModel
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    ANSWER_REQUESTS_PER_MINUTE,
    ANSWER_TOKENS_PER_MINUTE,
    GEMINI_ANSWER_MODEL,
    MONGODB_DATABASE,
    MissingConfigError,
    load_environment,
)
from retrieval.gemini_errors import GeminiRequestError
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact
from retrieval.search_results import SearchRefusedError, SearchResult

from evaluation.faithfulness_inputs import (
    AnswerRunFormatError,
    AnswerToJudge,
    ChangedSourceError,
    answers_to_judge,
    fetch_answer_sources,
)
from evaluation.faithfulness_judge import JudgmentRejectedError, judge_answer
from evaluation.faithfulness_metrics import (
    FaithfulnessScore,
    JudgingOutcome,
    summarize_faithfulness,
)
from evaluation.faithfulness_results import (
    build_faithfulness_details,
    render_faithfulness_table,
)
from evaluation.results import relative_path, result_paths, write_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "eval" / "results"

MILLISECONDS_PER_SECOND = 1000


class _DailyQuotaUsedUp(Exception):
    """Raised when Gemini's daily quota stops the run part way."""


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        outcome = _evaluate(arguments.answers, arguments.output_directory, arguments.max_answers)
    except (SearchRefusedError, AnswerRunFormatError, ChangedSourceError) as error:
        _print(f"Refused: {error}")
        return 1
    except _DailyQuotaUsedUp:
        _print("Stopped: a daily Gemini quota was used up before the first reply was judged.")
        return 1
    except (MissingConfigError, PyMongoError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    json_path, table_path, table, is_complete = outcome
    for line in table.splitlines():
        _print(line)
    _print("")
    _print(f"Saved {_relative(json_path)} and {_relative(table_path)}")
    if not is_complete:
        _print("Stopped: a daily Gemini quota is used up; the saved results are marked incomplete.")
        return 1
    return 0


def _evaluate(
    answers_path: Path, output_directory: Path, max_answers: int | None
) -> tuple[Path, Path, str, bool]:
    run_at = datetime.now(UTC)
    answer_run = json.loads(answers_path.read_text(encoding="utf-8"))
    answers = answers_to_judge(answer_run)
    if max_answers is not None:
        answers = answers[:max_answers]
    if not answers:
        raise AnswerRunFormatError("The answers file has no answered replies to judge")
    database = build_mongo_client()[MONGODB_DATABASE]
    sources_by_answer = fetch_answer_sources(database, answer_run["run"], answers)
    judge_model = GeminiAnswerModel(
        build_gemini_client(), RateLimiter(ANSWER_REQUESTS_PER_MINUTE, ANSWER_TOKENS_PER_MINUTE)
    )
    records: list[dict[str, Any]] = []
    scores_by_setup: dict[str, list[FaithfulnessScore]] = {}
    is_complete = True
    for answer, sources in zip(answers, sources_by_answer, strict=True):
        try:
            record, score = _judge(judge_model, answer, sources)
        except _DailyQuotaUsedUp:
            is_complete = False
            break
        records.append(record)
        scores_by_setup.setdefault(answer.setup, []).append(score)
    if not records:
        raise _DailyQuotaUsedUp()
    summaries = {
        setup: summarize_faithfulness(setup, scores) for setup, scores in scores_by_setup.items()
    }
    details = build_faithfulness_details(
        run_at, answers_path, answer_run, GEMINI_ANSWER_MODEL, PROJECT_ROOT
    )
    table = render_faithfulness_table(details, summaries, len(records), len(answers))
    json_path, table_path = result_paths(output_directory, run_at, kind="faithfulness")
    write_json(
        json_path,
        {
            "run": asdict(details),
            "complete": is_complete,
            "judged_answer_count": len(records),
            "answer_count": len(answers),
            "answers": records,
        },
    )
    table_path.write_text(table, encoding="utf-8")
    return json_path, table_path, table, is_complete


def _judge(
    judge_model: AnswerModel, answer: AnswerToJudge, sources: list[SearchResult]
) -> tuple[dict[str, Any], FaithfulnessScore]:
    started = time.perf_counter()
    base_record: dict[str, Any] = {"id": answer.question_id, "setup": answer.setup}
    try:
        judgment = judge_answer(answer.sentences, sources, judge_model)
    except JudgmentRejectedError as error:
        score = _unjudged_score(answer, JudgingOutcome.REJECTED)
        return {**base_record, **_unjudged_record(score, error.problems, started)}, score
    except GeminiRequestError as error:
        if error.is_daily_quota_exhausted:
            raise _DailyQuotaUsedUp() from error
        score = _unjudged_score(answer, JudgingOutcome.FAILED)
        problem = f"{type(error).__name__}: {error}"
        return {**base_record, **_unjudged_record(score, [problem], started)}, score
    score = FaithfulnessScore(
        question_id=answer.question_id,
        setup=answer.setup,
        outcome=JudgingOutcome.JUDGED,
        verdicts=[verdict.verdict for verdict in judgment.verdicts],
        uncited_sentence_count=judgment.uncited_sentence_count,
        prompt_tokens=judgment.reply.prompt_tokens,
        request_count=judgment.reply.attempt_count,
    )
    record = {
        **base_record,
        "outcome": str(score.outcome),
        "verdicts": [
            {
                "sentence": verdict.sentence_number,
                "text": answer.sentences[verdict.sentence_number - 1].text,
                "reason": verdict.reason,
                "verdict": str(verdict.verdict),
            }
            for verdict in judgment.verdicts
        ],
        "uncited_sentence_count": judgment.uncited_sentence_count,
        "prompt_tokens": judgment.reply.prompt_tokens,
        "output_tokens": judgment.reply.output_tokens,
        "request_count": judgment.reply.attempt_count,
        "judge_milliseconds": round(_milliseconds_since(started), 1),
    }
    return record, score


def _unjudged_score(answer: AnswerToJudge, outcome: JudgingOutcome) -> FaithfulnessScore:
    return FaithfulnessScore(
        question_id=answer.question_id,
        setup=answer.setup,
        outcome=outcome,
        verdicts=[],
        uncited_sentence_count=0,
        prompt_tokens=None,
        request_count=1,
    )


def _unjudged_record(
    score: FaithfulnessScore, problems: list[str], started: float
) -> dict[str, Any]:
    return {
        "outcome": str(score.outcome),
        "problems": problems,
        "request_count": score.request_count,
        "judge_milliseconds": round(_milliseconds_since(started), 1),
    }


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _relative(path: Path) -> str:
    return relative_path(path, PROJECT_ROOT)


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("answers", type=Path, help="an answers JSON file in eval/results/")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help="where to save the results (default eval/results/)",
    )
    parser.add_argument(
        "--max-answers",
        type=_positive_count,
        default=None,
        help="judge only the first this many answered replies (default: all)",
    )
    return parser.parse_args()


def _positive_count(text: str) -> int:
    count = int(text)
    if count < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return count


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
