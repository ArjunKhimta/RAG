"""Saving an evaluation run: its details, every question's results, and the summary table.

Each run writes two files to `eval/results/`, named by the time it started in UTC:
- `<time>-retrieval.json`: every question's sources for every setup, as locations only (chunk
  ID, file, definition, lines), never code text, plus the scores and the run details
- `<time>-retrieval.md`: the evaluation table, overall and split by kind of question

The run details make a result reproducible: when it ran, the project commit and whether there
were uncommitted changes, a SHA-256 of the question file, the indexed repository commit, and the
models used.

Every split is a group of questions: all answerable ones, then by origin (written from the code,
or rewritten from Stack Overflow), by style (a code name, or plain English), and by how many
definitions the question expects.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from retrieval.answer_generation import AnswerSentence
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_MODEL,
    RERANKER_MODEL_REPOSITORY,
    RERANKER_MODEL_REVISION,
)
from retrieval.question_set import EvaluationQuestion, QuestionSet
from retrieval.search_results import SearchResult

from evaluation.retrieval_metrics import QuestionScore, SetupSummary, summarize_scores

RESULT_TIME_FORMAT = "%Y-%m-%d-%H%M"

WRITTEN_FROM_CODE = "written_from_code"

STACK_OVERFLOW = "stack_overflow"

IDENTIFIER_STYLE = "identifier"

PERCENT = 100


@dataclass(frozen=True)
class RunDetails:
    run_at: str
    project_commit: str
    project_has_uncommitted_changes: bool
    question_file: str
    question_file_sha256: str
    repository: str
    version: str
    indexed_commit: str
    embedding_model: str
    embedding_dimensions: int
    reranker_model: str
    reranker_revision: str
    answer_model: str | None = None


@dataclass(frozen=True)
class QuestionGroup:
    label: str
    question_ids: list[str]


def build_run_details(
    run_at: datetime,
    question_path: Path,
    question_set: QuestionSet,
    repository_record: dict[str, Any],
    project_root: Path,
    answer_model: str | None = None,
) -> RunDetails:
    commit, has_changes = project_commit_state(project_root)
    return RunDetails(
        run_at=run_at.isoformat(timespec="seconds"),
        project_commit=commit,
        project_has_uncommitted_changes=has_changes,
        question_file=relative_path(question_path, project_root),
        question_file_sha256=file_sha256(question_path),
        repository=question_set.repository,
        version=question_set.version,
        indexed_commit=repository_record["commit_id"],
        embedding_model=GEMINI_EMBEDDING_MODEL,
        embedding_dimensions=EMBEDDING_DIMENSIONS,
        reranker_model=RERANKER_MODEL_REPOSITORY,
        reranker_revision=RERANKER_MODEL_REVISION,
        answer_model=answer_model,
    )


def relative_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(project_root):
        return resolved.relative_to(project_root).as_posix()
    return str(path)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_commit_state(project_root: Path) -> tuple[str, bool]:
    """Return the current commit ID and whether the working tree has uncommitted changes."""
    commit = _git_output(project_root, ["rev-parse", "HEAD"])
    changes = _git_output(project_root, ["status", "--porcelain"])
    return commit, bool(changes)


def question_groups(questions: list[EvaluationQuestion]) -> list[QuestionGroup]:
    from_code = [question for question in questions if question.origin == WRITTEN_FROM_CODE]
    from_stack_overflow = [question for question in questions if question.origin == STACK_OVERFLOW]
    names = [question for question in questions if question.query_style == IDENTIFIER_STYLE]
    plain_english = [
        question for question in questions if question.query_style != IDENTIFIER_STYLE
    ]
    one_expected = [question for question in questions if len(question.expected) == 1]
    several_expected = [question for question in questions if len(question.expected) >= 2]
    return [
        QuestionGroup("all answerable", _ids_of(questions)),
        QuestionGroup("written from the code", _ids_of(from_code)),
        QuestionGroup("from Stack Overflow", _ids_of(from_stack_overflow)),
        QuestionGroup("name questions", _ids_of(names)),
        QuestionGroup("plain-English questions", _ids_of(plain_english)),
        QuestionGroup("one expected definition", _ids_of(one_expected)),
        QuestionGroup("two or more expected definitions", _ids_of(several_expected)),
    ]


def group_summaries(
    groups: list[QuestionGroup], scores_by_setup: dict[str, list[QuestionScore]]
) -> dict[str, dict[str, SetupSummary]]:
    """Summarize every setup within every non-empty group, keyed by group label, then setup."""
    summaries: dict[str, dict[str, SetupSummary]] = {}
    for group in groups:
        wanted_ids = set(group.question_ids)
        if not wanted_ids:
            continue
        summaries[group.label] = {
            setup: summarize_scores(
                setup, [score for score in scores if score.question_id in wanted_ids]
            )
            for setup, scores in scores_by_setup.items()
        }
    return summaries


def source_location(source: SearchResult) -> dict[str, Any]:
    return {
        "chunk_id": source.chunk_id,
        "file_path": source.file_path,
        "qualified_name": source.qualified_name,
        "kind": source.kind,
        "start_line": source.start_line,
        "end_line": source.end_line,
    }


def sentence_records(sentences: list[AnswerSentence]) -> list[dict[str, Any]]:
    """Each answer sentence as the model wrote it, without markers, with its own citations."""
    return [
        {
            "text": sentence.text,
            "citations": [
                {
                    "source_number": citation.source_number,
                    "start_line": citation.start_line,
                    "end_line": citation.end_line,
                }
                for citation in sentence.citations
            ],
        }
        for sentence in sentences
    ]


def result_paths(
    output_directory: Path, run_at: datetime, kind: str = "retrieval"
) -> tuple[Path, Path]:
    stem = f"{run_at.strftime(RESULT_TIME_FORMAT)}-{kind}"
    return output_directory / f"{stem}.json", output_directory / f"{stem}.md"


def write_json(path: Path, content: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def details_as_dict(details: RunDetails) -> dict[str, Any]:
    return asdict(details)


def summary_as_dict(summary: SetupSummary) -> dict[str, Any]:
    return {**asdict(summary), "recall": summary.recall}


def render_retrieval_table(
    details: RunDetails,
    summaries: dict[str, dict[str, SetupSummary]],
    sources_by_setup: dict[str, int],
    skipped_ids: list[str],
    embedding_requests: int,
) -> str:
    overall = summaries["all answerable"]
    any_summary = next(iter(overall.values()))
    lines = [
        f"# Retrieval evaluation: {details.repository} {details.version}",
        "",
        *_details_lines(details, any_summary.question_count, skipped_ids, embedding_requests),
        "",
        "## All answerable questions",
        "",
        "| Setup | Sources | Recall | Complete | MRR | Median ms | 90th percentile ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for setup, summary in overall.items():
        lines.append(
            f"| {setup} | {sources_by_setup[setup]} | {_percent(summary.recall)} "
            f"({summary.found_total}/{summary.expected_total}) | "
            f"{summary.complete_count}/{summary.question_count} | "
            f"{summary.mean_reciprocal_rank:.2f} | {summary.median_milliseconds:.0f} | "
            f"{summary.slow_milliseconds:.0f} |"
        )
    group_labels = [label for label in summaries if label != "all answerable"]
    lines.extend(
        [
            "",
            "## Recall by kind of question",
            "",
            "Each cell is recall, with complete questions in brackets.",
            "",
            _table_row(["Setup", *(_group_heading(label, summaries) for label in group_labels)]),
            "|---|" + "---|" * len(group_labels),
        ]
    )
    for setup in overall:
        cells = [
            f"{_percent(summaries[label][setup].recall)} "
            f"({summaries[label][setup].complete_count}/{summaries[label][setup].question_count})"
            for label in group_labels
        ]
        lines.append(_table_row([setup, *cells]))
    lines.extend(["", *_measure_notes()])
    return "\n".join(lines) + "\n"


def _details_lines(
    details: RunDetails, question_count: int, skipped_ids: list[str], embedding_requests: int
) -> list[str]:
    changes = " (with uncommitted changes)" if details.project_has_uncommitted_changes else ""
    return [
        f"- Run at {details.run_at}, project commit `{details.project_commit[:12]}`{changes}",
        f"- Questions: {question_count} answerable from `{details.question_file}` "
        f"(SHA-256 `{details.question_file_sha256[:12]}`); skipped, answer not in the code: "
        f"{', '.join(skipped_ids) or 'none'}",
        f"- Index: {details.repository} at commit `{details.indexed_commit[:12]}`, "
        f"{details.embedding_model} at {details.embedding_dimensions} dimensions",
        f"- Reranker: {details.reranker_model} at revision `{details.reranker_revision[:12]}`",
        f"- Embedding requests made by this run: {embedding_requests}",
    ]


def _group_heading(label: str, summaries: dict[str, dict[str, SetupSummary]]) -> str:
    question_count = next(iter(summaries[label].values())).question_count
    return f"{label} ({question_count})"


def _measure_notes() -> list[str]:
    return [
        "Recall: expected definitions found among the sources the answer model sees, pooled over "
        "questions. Complete: questions with every expected definition found. MRR: mean of 1 / "
        "the rank of the first source covering an expected definition (0 when none). Times are "
        "wall-clock per question for the whole setup, with question embeddings from the cache. "
        "A single run on 50 handwritten questions: a sanity-level evaluation, not a benchmark.",
    ]


def _ids_of(questions: list[EvaluationQuestion]) -> list[str]:
    return [question.question_id for question in questions]


def _table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _percent(fraction: float) -> str:
    return f"{fraction * PERCENT:.1f}%"


def _git_output(project_root: Path, arguments: list[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=project_root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()
