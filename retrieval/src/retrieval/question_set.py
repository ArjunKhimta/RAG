"""Load a handwritten question file and check retrieved code and answers against its expectations.

An expected definition counts as found when a source from the same file, with the same qualified
name, overlaps its lines. The name check stops a class outline, whose lines span its methods but
hide their bodies, from counting as one of those methods. The line check tells apart definitions
that share a name, such as `typing.overload` stubs and the real function. Any part of a split
definition counts.

An answer counts as citing an expected definition when one of its citations names such a source
and its lines overlap the definition.

Some questions cannot be answered from the code, such as an import error caused by mismatched
package versions. They have `answer_in_code` set to false and no expected definitions, and a
correct reply says the sources do not contain the answer and cites nothing.

Each question also records where it came from (`origin`: written from the code, or taken from
Stack Overflow), the original's link (`source_url`), and who wrote the wording (`written_by`), so
results can be compared by source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from retrieval.answer_generation import Citation, GeneratedAnswer
from retrieval.search_results import SearchResult


@dataclass(frozen=True)
class ExpectedDefinition:
    """A definition an answer needs. Lines are 1-indexed and inclusive, covering all of it."""

    file_path: str
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class EvaluationQuestion:
    question_id: str
    question: str
    query_style: str
    expected: tuple[ExpectedDefinition, ...]
    answer_in_code: bool = True
    origin: str | None = None
    source_url: str | None = None
    written_by: str | None = None


class QuestionFileError(ValueError):
    """Raised when a question's expectations contradict each other."""


@dataclass(frozen=True)
class QuestionSet:
    repository: str
    version: str
    commit_id: str
    questions: tuple[EvaluationQuestion, ...]


def load_question_set(path: Path) -> QuestionSet:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = tuple(_question_from(item) for item in data["questions"])
    return QuestionSet(
        repository=data["repository"],
        version=data["version"],
        commit_id=data["commit_id"],
        questions=questions,
    )


def is_correct_refusal(generated: GeneratedAnswer) -> bool:
    """A question the code cannot answer is handled correctly by saying so and citing nothing."""
    return not generated.found_answer and not generated.citations


def _question_from(item: dict[str, Any]) -> EvaluationQuestion:
    question = EvaluationQuestion(
        question_id=item["id"],
        question=item["question"],
        query_style=item["query_style"],
        expected=tuple(
            ExpectedDefinition(
                file_path=expected["file_path"],
                qualified_name=expected["qualified_name"],
                start_line=expected["start_line"],
                end_line=expected["end_line"],
            )
            for expected in item["expected"]
        ),
        answer_in_code=item.get("answer_in_code", True),
        origin=item.get("origin"),
        source_url=item.get("source_url"),
        written_by=item.get("written_by"),
    )
    if question.answer_in_code and not question.expected:
        raise QuestionFileError(f"{question.question_id} expects an answer but lists no code")
    if not question.answer_in_code and question.expected:
        raise QuestionFileError(f"{question.question_id} has no answer in the code but lists code")
    return question


def is_covered_by(expected: ExpectedDefinition, source: SearchResult) -> bool:
    same_definition = (
        source.file_path == expected.file_path
        and source.qualified_name == expected.qualified_name
    )
    return same_definition and _overlaps(
        source.start_line, source.end_line, expected.start_line, expected.end_line
    )


def found_definitions(
    expected_definitions: tuple[ExpectedDefinition, ...], sources: list[SearchResult]
) -> list[ExpectedDefinition]:
    """Return the expected definitions that at least one source covers, in their own order."""
    return [
        expected
        for expected in expected_definitions
        if any(is_covered_by(expected, source) for source in sources)
    ]


def cited_definitions(
    expected_definitions: tuple[ExpectedDefinition, ...],
    citations: list[Citation],
    sources: list[SearchResult],
) -> list[ExpectedDefinition]:
    """Return the expected definitions that at least one citation points into."""
    return [
        expected
        for expected in expected_definitions
        if any(_citation_points_into(expected, citation, sources) for citation in citations)
    ]


def _citation_points_into(
    expected: ExpectedDefinition, citation: Citation, sources: list[SearchResult]
) -> bool:
    if not 1 <= citation.source_number <= len(sources):
        return False
    source = sources[citation.source_number - 1]
    lines_overlap = _overlaps(
        citation.start_line, citation.end_line, expected.start_line, expected.end_line
    )
    return is_covered_by(expected, source) and lines_overlap


def _overlaps(first_start: int, first_end: int, second_start: int, second_end: int) -> bool:
    return first_start <= second_end and second_start <= first_end
