"""Compare answering with and without call graph expansion on a handwritten question file.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/compare_expansion.py eval/questions/pallets-flask-3.1.3.json
    python retrieval/scripts/compare_expansion.py eval/questions/pallets-flask-3.1.3.json --answers

For each question, finds the sources once, as `ask_repository.py` does (routing code names to
keyword search), then expands those sources through the call graph. Reports context recall both
ways: how many expected definitions reach the answer model, by the rules in `question_set.py`. This
costs no generation requests, and one embedding request for each question not asked before.

`--answers` also asks Gemini both ways, 2 generation requests per question (more if a temporary
failure is retried), and reports whether each answer cites every expected definition, its prompt
tokens, and its time. A rejected answer counts as citing nothing. A used-up daily quota stops the
run, and the questions finished so far are still reported.

Questions whose answer is not in the code are skipped, because there is no code to find. This is a
sanity check on a few questions, not the evaluation. Exits 0 when every question ran,
and 1 on a refusal or when a daily quota stopped the run. Every printed line passes through the
redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from pymongo.errors import PyMongoError

from retrieval.answer_generation import (
    AnswerModel,
    AnswerRejectedError,
    GeminiAnswerModel,
    generate_answer,
)
from retrieval.answer_sources import ExpandedSources, expand_sources, find_sources
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    ANSWER_REQUESTS_PER_MINUTE,
    ANSWER_TOKENS_PER_MINUTE,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import GeminiQueryEmbedder
from retrieval.gemini_errors import GeminiRequestError
from retrieval.graph_expansion import require_call_graph
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.query_router import route_query
from retrieval.question_set import (
    EvaluationQuestion,
    QuestionSet,
    cited_definitions,
    found_definitions,
    load_question_set,
)
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError, verified_reranker_files
from retrieval.reranking import CrossEncoderScorer, QuestionTooLongError
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, SearchResult, require_indexed_version
from retrieval.vector_search import require_searchable_version

MILLISECONDS_PER_SECOND = 1000

QUESTION_DISPLAY_LENGTH = 90


class _DailyQuotaUsedUp(Exception):
    """Raised when Gemini's daily quota stops the run part way."""


@dataclass(frozen=True)
class AnswerCheck:
    """One answer's outcome. `outcome` is "answered", "no answer", "rejected", or "failed".

    `milliseconds` includes any wait for the per-minute request limit. `problems` lists why a
    rejected answer failed the reference check.
    """

    outcome: str
    cited_count: int
    prompt_tokens: int | None
    milliseconds: float
    request_count: int
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class QuestionComparison:
    question: EvaluationQuestion
    base_found_count: int
    expanded_found_count: int
    expansion: ExpandedSources
    base_answer: AnswerCheck | None
    expanded_answer: AnswerCheck | None


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    question_set = load_question_set(arguments.questions)
    comparisons: list[QuestionComparison] = []
    stopped_by_quota = False
    try:
        comparisons, stopped_by_quota = _compare_all(question_set, arguments.answers)
    except (SearchRefusedError, QuestionTooLongError) as error:
        _print(f"Refused: {error}")
        return 1
    except (MissingConfigError, PyMongoError, RerankerModelError, GeminiRequestError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    for line in _report_lines(question_set, comparisons, arguments.answers):
        _print(line)
    if stopped_by_quota:
        _print("")
        _print("Stopped: a daily Gemini quota is used up; the questions above are complete.")
        return 1
    return 0


def _compare_all(
    question_set: QuestionSet, with_answers: bool
) -> tuple[list[QuestionComparison], bool]:
    database = build_mongo_client()[MONGODB_DATABASE]
    store = MongoChunkStore(database)
    record = store.find_repository_record(question_set.repository, question_set.version)
    require_indexed_version(record, question_set.repository, question_set.version)
    require_call_graph(record, question_set.repository, question_set.version)
    if record["commit_id"] != question_set.commit_id:
        raise SearchRefusedError(
            f"The questions were written for commit {question_set.commit_id}, but "
            f"{question_set.repository} at {question_set.version} is indexed at "
            f"{record['commit_id']}"
        )
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    gemini_client = build_gemini_client()
    embedder = CachingQueryEmbedder(GeminiQueryEmbedder(gemini_client), query_embedding_store)
    require_searchable_version(record, question_set.repository, question_set.version, embedder)
    chunks_collection = database[CHUNKS_COLLECTION]
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    scorer = CrossEncoderScorer(verified_reranker_files())
    answer_model = GeminiAnswerModel(
        gemini_client, RateLimiter(ANSWER_REQUESTS_PER_MINUTE, ANSWER_TOKENS_PER_MINUTE)
    )
    comparisons: list[QuestionComparison] = []
    for question in _answerable(question_set):
        found = find_sources(
            chunks_collection,
            embedder,
            scorer,
            question.question,
            question_set.repository,
            question_set.version,
            route_query(question.question),
        )
        expansion = expand_sources(
            chunks_collection,
            scorer,
            question.question,
            found.sources,
            question_set.repository,
            question_set.version,
        )
        base_answer = None
        expanded_answer = None
        if with_answers:
            try:
                base_answer = _check_answer(question, found.sources, None, answer_model)
                expanded_answer = _check_answer(
                    question, expansion.sources, expansion.related_notes, answer_model
                )
            except _DailyQuotaUsedUp:
                return comparisons, True
        comparisons.append(
            QuestionComparison(
                question=question,
                base_found_count=len(found_definitions(question.expected, found.sources)),
                expanded_found_count=len(found_definitions(question.expected, expansion.sources)),
                expansion=expansion,
                base_answer=base_answer,
                expanded_answer=expanded_answer,
            )
        )
    return comparisons, False


def _answerable(question_set: QuestionSet) -> list[EvaluationQuestion]:
    return [question for question in question_set.questions if question.answer_in_code]


def _check_answer(
    question: EvaluationQuestion,
    sources: list[SearchResult],
    related_notes: list[str | None] | None,
    answer_model: AnswerModel,
) -> AnswerCheck:
    started = time.perf_counter()
    try:
        generated = generate_answer(question.question, sources, answer_model, related_notes)
    except AnswerRejectedError as error:
        return AnswerCheck(
            "rejected", 0, None, _milliseconds_since(started), 1, tuple(error.problems)
        )
    except GeminiRequestError as error:
        if error.is_daily_quota_exhausted:
            raise _DailyQuotaUsedUp() from error
        return AnswerCheck("failed", 0, None, _milliseconds_since(started), 1)
    cited = cited_definitions(question.expected, generated.citations, generated.sources)
    return AnswerCheck(
        outcome="answered" if generated.found_answer else "no answer",
        cited_count=len(cited),
        prompt_tokens=generated.reply.prompt_tokens,
        milliseconds=_milliseconds_since(started),
        request_count=generated.reply.attempt_count,
    )


def _report_lines(
    question_set: QuestionSet, comparisons: list[QuestionComparison], with_answers: bool
) -> list[str]:
    skipped_count = len(question_set.questions) - len(_answerable(question_set))
    lines = [
        f"Questions   {len(comparisons)} of {len(question_set.questions)} from "
        f"{question_set.repository} at {question_set.version} "
        f"({skipped_count} skipped: answer not in the code)",
        "Base        keyword top 5 for a code name, else hybrid top 30 reranked to 5",
        "Expanded    base plus the best 3 callers or callees, picked by the reranker",
        "",
    ]
    for comparison in comparisons:
        lines.extend(_question_lines(comparison, with_answers))
    lines.extend(_summary_lines(comparisons, with_answers))
    return lines


def _question_lines(comparison: QuestionComparison, with_answers: bool) -> list[str]:
    question = comparison.question
    expected_count = len(question.expected)
    expansion = comparison.expansion
    kept = ", ".join(
        f"{kept.neighbor.result.qualified_name} ({kept.note})"
        for kept in expansion.kept_neighbors
    )
    lines = [
        f"{question.question_id}  {_shortened(question.question)}",
        f"  expected found   base {comparison.base_found_count}/{expected_count}   "
        f"expanded {comparison.expanded_found_count}/{expected_count}",
        f"  neighbors        {expansion.found_neighbor_count} found, kept: {kept or 'none'}",
        f"  expansion time   {_expansion_milliseconds(expansion):.0f} ms",
    ]
    if with_answers:
        base_answer = _describe_answer(comparison.base_answer, expected_count)
        expanded_answer = _describe_answer(comparison.expanded_answer, expected_count)
        lines.append(f"  base answer      {base_answer}")
        lines.extend(_problem_lines(comparison.base_answer))
        lines.append(f"  expanded answer  {expanded_answer}")
        lines.extend(_problem_lines(comparison.expanded_answer))
    lines.append("")
    return lines


def _summary_lines(comparisons: list[QuestionComparison], with_answers: bool) -> list[str]:
    if not comparisons:
        return ["No questions completed."]
    expected_total = sum(len(comparison.question.expected) for comparison in comparisons)
    base_found = sum(comparison.base_found_count for comparison in comparisons)
    expanded_found = sum(comparison.expanded_found_count for comparison in comparisons)
    base_complete = sum(1 for comparison in comparisons if _base_complete(comparison))
    expanded_complete = sum(1 for comparison in comparisons if _expanded_complete(comparison))
    expansion_times = [_expansion_milliseconds(comparison.expansion) for comparison in comparisons]
    lines = [
        "Summary",
        f"  Expected definitions reaching the model   base {base_found}/{expected_total}   "
        f"expanded {expanded_found}/{expected_total}",
        f"  Questions with every definition present   base {base_complete}/{len(comparisons)}   "
        f"expanded {expanded_complete}/{len(comparisons)}",
        f"  Expansion time (graph and reranking)      {min(expansion_times):.0f} to "
        f"{max(expansion_times):.0f} ms",
    ]
    if with_answers:
        lines.extend(_answer_summary_lines(comparisons))
    return lines


def _answer_summary_lines(comparisons: list[QuestionComparison]) -> list[str]:
    base_checks = [comparison.base_answer for comparison in comparisons if comparison.base_answer]
    expanded_checks = [
        comparison.expanded_answer for comparison in comparisons if comparison.expanded_answer
    ]
    base_cites_all = sum(
        1
        for comparison in comparisons
        if comparison.base_answer
        and comparison.base_answer.cited_count == len(comparison.question.expected)
    )
    expanded_cites_all = sum(
        1
        for comparison in comparisons
        if comparison.expanded_answer
        and comparison.expanded_answer.cited_count == len(comparison.question.expected)
    )
    request_count = sum(check.request_count for check in [*base_checks, *expanded_checks])
    return [
        f"  Answers citing every expected definition  base {base_cites_all}/{len(base_checks)}   "
        f"expanded {expanded_cites_all}/{len(expanded_checks)}",
        f"  Answer outcomes                           base {_outcome_counts(base_checks)}   "
        f"expanded {_outcome_counts(expanded_checks)}",
        f"  Prompt tokens                             base {_token_range(base_checks)}   "
        f"expanded {_token_range(expanded_checks)}",
        f"  Answer time, including rate-limit waits  base {_time_range(base_checks)}   "
        f"expanded {_time_range(expanded_checks)}",
        f"  Generation requests                       {request_count}",
    ]


def _describe_answer(check: AnswerCheck | None, expected_count: int) -> str:
    if check is None:
        return "not run"
    tokens = "tokens not reported"
    if check.prompt_tokens is not None:
        tokens = f"{check.prompt_tokens} tokens"
    return (
        f"{check.outcome}, cites {check.cited_count}/{expected_count}, {tokens}, "
        f"{check.milliseconds / MILLISECONDS_PER_SECOND:.1f} s"
    )


def _problem_lines(check: AnswerCheck | None) -> list[str]:
    if check is None:
        return []
    return [f"                   - {problem}" for problem in check.problems]


def _outcome_counts(checks: list[AnswerCheck]) -> str:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.outcome] = counts.get(check.outcome, 0) + 1
    return ", ".join(f"{outcome} {count}" for outcome, count in sorted(counts.items()))


def _token_range(checks: list[AnswerCheck]) -> str:
    token_counts = [check.prompt_tokens for check in checks if check.prompt_tokens is not None]
    if not token_counts:
        return "none reported"
    return f"{min(token_counts)} to {max(token_counts)}"


def _time_range(checks: list[AnswerCheck]) -> str:
    seconds = [check.milliseconds / MILLISECONDS_PER_SECOND for check in checks]
    if not seconds:
        return "none"
    return f"{min(seconds):.1f} to {max(seconds):.1f} s"


def _base_complete(comparison: QuestionComparison) -> bool:
    return comparison.base_found_count == len(comparison.question.expected)


def _expanded_complete(comparison: QuestionComparison) -> bool:
    return comparison.expanded_found_count == len(comparison.question.expected)


def _expansion_milliseconds(expansion: ExpandedSources) -> float:
    return sum(expansion.timings.values())


def _shortened(text: str) -> str:
    if len(text) <= QUESTION_DISPLAY_LENGTH:
        return text
    return text[: QUESTION_DISPLAY_LENGTH - 3] + "..."


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("questions", type=Path, help="a question file in eval/questions/")
    parser.add_argument(
        "--answers",
        action="store_true",
        help="also ask Gemini both ways: 2 generation requests per question",
    )
    return parser.parse_args()


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
