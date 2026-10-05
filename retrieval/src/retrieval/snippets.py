"""Short cited snippets and GitHub links, shared by the ask script and the retrieval service.

Answers show short snippets with a link back, never whole files: a snippet holds at most
`MAX_SNIPPET_LINES` of the cited lines, with their real line numbers, and counts what is left
for the link. A class outline has no real line numbers, since its method bodies were replaced
with `...`, so its snippet shows the outline's opening lines without numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from retrieval.answer_generation import Citation, is_outline, source_code_lines
from retrieval.search_results import SearchResult

MAX_SNIPPET_LINES = 12

GITHUB_LINE_LINK = "https://github.com/{repository}/blob/{commit_id}/{file_path}#L{start}-L{end}"

GITHUB_FILE_LINK = "https://github.com/{repository}/blob/{commit_id}/{file_path}"


@dataclass(frozen=True)
class SnippetLine:
    number: int | None
    text: str


@dataclass(frozen=True)
class Snippet:
    is_outline: bool
    lines: list[SnippetLine]
    more_line_count: int


def cited_snippet(
    source: SearchResult, citation: Citation, max_lines: int = MAX_SNIPPET_LINES
) -> Snippet:
    code_lines = source_code_lines(source)
    if is_outline(source):
        shown = code_lines[:max_lines]
        return Snippet(
            is_outline=True,
            lines=[SnippetLine(number=None, text=line) for line in shown],
            more_line_count=len(code_lines) - len(shown),
        )
    first_index = citation.start_line - source.start_line
    last_index = citation.end_line - source.start_line
    cited = code_lines[first_index : last_index + 1]
    shown = cited[:max_lines]
    return Snippet(
        is_outline=False,
        lines=[
            SnippetLine(number=number, text=line)
            for number, line in enumerate(shown, start=citation.start_line)
        ],
        more_line_count=len(cited) - len(shown),
    )


def github_line_link(
    repository: str, commit_id: str, file_path: str, start_line: int, end_line: int
) -> str:
    return GITHUB_LINE_LINK.format(
        repository=repository,
        commit_id=commit_id,
        file_path=file_path,
        start=start_line,
        end=end_line,
    )


def github_file_link(repository: str, commit_id: str, file_path: str) -> str:
    return GITHUB_FILE_LINK.format(repository=repository, commit_id=commit_id, file_path=file_path)


def unique_citations(citations: list[Citation]) -> list[Citation]:
    return list(dict.fromkeys(citations))
