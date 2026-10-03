from __future__ import annotations

from retrieval.question_set import EvaluationQuestion, ExpectedDefinition
from retrieval.search_results import SearchResult

APP_FILE = "src/pkg/app.py"


def expected_definition(qualified_name: str, start_line: int, end_line: int) -> ExpectedDefinition:
    return ExpectedDefinition(APP_FILE, qualified_name, start_line, end_line)


def question(
    question_id: str,
    expected: tuple[ExpectedDefinition, ...],
    origin: str = "stack_overflow",
    query_style: str = "natural_language",
) -> EvaluationQuestion:
    return EvaluationQuestion(
        question_id=question_id,
        question=f"question {question_id}",
        query_style=query_style,
        expected=expected,
        origin=origin,
    )


def source(qualified_name: str, start_line: int, end_line: int) -> SearchResult:
    return SearchResult(
        chunk_id=f"{qualified_name}:{start_line}",
        file_path=APP_FILE,
        start_line=start_line,
        end_line=end_line,
        kind="function",
        qualified_name=qualified_name,
        signature=None,
        part_number=1,
        part_count=1,
        is_test_file=False,
        text="def placeholder():\n    pass",
        score=1.0,
    )
