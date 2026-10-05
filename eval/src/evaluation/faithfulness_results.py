"""The faithfulness evaluation's run details and summary table.

A faithfulness run judges a saved answer run, so its details name that run: the answers file and
its SHA-256, when the answers were written and by which model, and the indexed commit, next to
this run's own time, project commit, and judge model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evaluation.faithfulness_metrics import FaithfulnessSummary
from evaluation.results import file_sha256, project_commit_state, relative_path

PERCENT = 100


@dataclass(frozen=True)
class FaithfulnessRunDetails:
    run_at: str
    project_commit: str
    project_has_uncommitted_changes: bool
    judge_model: str
    answers_file: str
    answers_file_sha256: str
    answers_run_at: str
    answer_model: str
    repository: str
    version: str
    indexed_commit: str


def build_faithfulness_details(
    run_at: datetime,
    answers_path: Path,
    answer_run: dict[str, Any],
    judge_model: str,
    project_root: Path,
) -> FaithfulnessRunDetails:
    commit, has_changes = project_commit_state(project_root)
    answers_run_details = answer_run["run"]
    return FaithfulnessRunDetails(
        run_at=run_at.isoformat(timespec="seconds"),
        project_commit=commit,
        project_has_uncommitted_changes=has_changes,
        judge_model=judge_model,
        answers_file=relative_path(answers_path, project_root),
        answers_file_sha256=file_sha256(answers_path),
        answers_run_at=answers_run_details["run_at"],
        answer_model=answers_run_details["answer_model"],
        repository=answers_run_details["repository"],
        version=answers_run_details["version"],
        indexed_commit=answers_run_details["indexed_commit"],
    )


def render_faithfulness_table(
    details: FaithfulnessRunDetails,
    summaries: dict[str, FaithfulnessSummary],
    completed_count: int,
    total_count: int,
) -> str:
    lines = [
        f"# Faithfulness evaluation: {details.repository} {details.version}",
        "",
        *_details_lines(details, completed_count, total_count),
        "",
        "## Sentences",
        "",
        _row(["Setup", "Sentences judged", "Supported", "Miscited", "Unsupported", "Uncited"]),
        _row(["---"] * 6),
    ]
    for setup, summary in summaries.items():
        lines.append(
            _row(
                [
                    setup,
                    str(summary.sentence_count),
                    _count_and_percent(summary.supported_count, summary.sentence_count),
                    _count_and_percent(summary.miscited_count, summary.sentence_count),
                    _count_and_percent(summary.unsupported_count, summary.sentence_count),
                    str(summary.uncited_sentence_count),
                ]
            )
        )
    lines.extend(
        [
            "",
            "## Answers",
            "",
            _row(
                [
                    "Setup",
                    "Answers judged",
                    "Every sentence supported",
                    "Any sentence unsupported",
                    "Rejected judgments",
                    "Failed",
                    "Median prompt tokens",
                    "Requests",
                ]
            ),
            _row(["---"] * 8),
        ]
    )
    for setup, summary in summaries.items():
        lines.append(
            _row(
                [
                    setup,
                    f"{summary.judged_count}/{summary.answer_count}",
                    _count_and_percent(summary.fully_supported_count, summary.judged_count),
                    _count_and_percent(summary.any_unsupported_count, summary.judged_count),
                    str(summary.rejected_count),
                    str(summary.failed_count),
                    _tokens(summary.median_prompt_tokens),
                    str(summary.request_count),
                ]
            )
        )
    lines.extend(["", *_measure_notes()])
    return "\n".join(lines) + "\n"


def _details_lines(
    details: FaithfulnessRunDetails, completed_count: int, total_count: int
) -> list[str]:
    changes = " (with uncommitted changes)" if details.project_has_uncommitted_changes else ""
    completeness = f"all {total_count} answered replies"
    if completed_count < total_count:
        completeness = f"INCOMPLETE: {completed_count} of {total_count} answered replies"
    return [
        f"- Run at {details.run_at}, project commit `{details.project_commit[:12]}`{changes}",
        f"- Judge model: {details.judge_model}",
        f"- Answers: {completeness} from `{details.answers_file}` "
        f"(SHA-256 `{details.answers_file_sha256[:12]}`), written at {details.answers_run_at} "
        f"by {details.answer_model}",
        f"- Index: {details.repository} at commit `{details.indexed_commit[:12]}`",
    ]


def _measure_notes() -> list[str]:
    return [
        "Each cited sentence of an answered reply gets one verdict. Supported: its cited lines "
        "show everything it says. Miscited: they do not, but the cited lines and the other "
        "sources together do. Unsupported: some part is shown by no source, including claims "
        "true of the real library but absent from the sources. Uncited sentences are not judged. "
        "Rejected judgments: the judge's reply did not give one verdict per sentence. The judge "
        "is the same model that wrote the answers, so it may be lenient on its own mistakes; its "
        "agreement with a hand check is reported separately. One run.",
    ]


def _count_and_percent(count: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{count}/{total} ({count / total * PERCENT:.0f}%)"


def _tokens(token_count: int | None) -> str:
    return "not reported" if token_count is None else str(token_count)


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"
