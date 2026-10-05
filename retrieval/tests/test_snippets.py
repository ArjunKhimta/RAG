from __future__ import annotations

from retrieval.answer_generation import Citation
from retrieval.search_results import SearchResult
from retrieval.snippets import cited_snippet, opening_snippet


def _result(start_line: int, line_count: int, kind: str = "function") -> SearchResult:
    return SearchResult(
        chunk_id="chunk",
        file_path="src/flask/app.py",
        start_line=start_line,
        end_line=start_line + line_count - 1,
        kind=kind,
        qualified_name="run",
        signature=None,
        part_number=1,
        part_count=1,
        is_test_file=False,
        text="\n".join(f"line {number}" for number in range(start_line, start_line + line_count)),
        score=0.9,
    )


def test_an_opening_snippet_shows_the_first_twelve_lines_with_real_numbers():
    snippet = opening_snippet(_result(start_line=100, line_count=30))

    assert [line.number for line in snippet.lines] == list(range(100, 112))
    assert snippet.lines[0].text == "line 100"
    assert snippet.more_line_count == 18
    assert snippet.is_outline is False


def test_a_short_result_is_shown_whole():
    snippet = opening_snippet(_result(start_line=7, line_count=3))

    assert [line.text for line in snippet.lines] == ["line 7", "line 8", "line 9"]
    assert snippet.more_line_count == 0


def test_an_outline_snippet_has_no_line_numbers():
    snippet = opening_snippet(_result(start_line=50, line_count=20, kind="class"))

    assert snippet.is_outline is True
    assert all(line.number is None for line in snippet.lines)
    assert len(snippet.lines) == 12


def test_a_cited_snippet_starts_at_the_citation():
    snippet = cited_snippet(_result(start_line=100, line_count=30), Citation(1, 110, 112))

    assert [line.number for line in snippet.lines] == [110, 111, 112]
    assert snippet.lines[0].text == "line 110"
