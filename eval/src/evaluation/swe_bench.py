"""Expected definitions for SWE-bench Lite tasks, worked out from each task's real fix.

A SWE-bench task is a GitHub issue, the commit the project was at when it was filed (the base
commit), and the patch its maintainers merged. Search is scored on whether it finds the code the
patch changed, so the expected definitions are the chunks at the base commit that hold:
- every line the patch removes or rewrites (a rewritten line shows as removed, then added), and
- for lines the patch only adds, the line just before the insertion (or, when that line belongs to
  no definition, such as a blank line between two functions, the line just after it).

Each line goes to the smallest chunk that contains it, so a line inside a method counts for the
method rather than the outline of its class, and a line in a class body outside any method counts
for the class. A split definition is expected as a whole, from its first part's first line to its
last part's last line, as the handwritten question file records definitions. Lines at module level,
outside any function or class, count for the module chunk that holds them; `split_module_level`
sets those apart, since a fix's import lines follow from its real change rather than locate it.

The patch is only read as text: nothing in it, or in the repository, is run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from retrieval.chunker import ChunkKind, CodeChunk, chunk_python_source

HUNK_HEADER_PATTERN = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

OLD_FILE_PREFIX = "--- "

NEW_FILE_PREFIX = "+++ "

OLD_PATH_PREFIX = "a/"

NEW_PATH_PREFIX = "b/"

MISSING_FILE = "/dev/null"

NO_NEWLINE_MARKER = "\\"

CHANGED_LINE_REASON = "changed lines"

INSERTION_REASON = "insertion point"


class PatchFormatError(ValueError):
    """Raised when a patch is not a unified diff this module can read."""


@dataclass(frozen=True)
class FileChange:
    """The base-commit lines one file's patch touches. Line numbers are 1-indexed.

    `insertion_points` holds, for each run of added lines not next to a removed line, the number
    of the old line the run follows; 0 means the start of the file.
    """

    file_path: str
    removed_lines: tuple[int, ...]
    insertion_points: tuple[int, ...]
    is_new_file: bool = False


@dataclass(frozen=True)
class ExpectedChange:
    file_path: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    reasons: tuple[str, ...]
    lines: tuple[int, ...] = field(default_factory=tuple)


def read_file_changes(patch: str) -> list[FileChange]:
    """Return what a unified diff changes, file by file, in base-commit line numbers."""
    changes: list[FileChange] = []
    lines = patch.splitlines()
    index = 0
    while index < len(lines):
        if not lines[index].startswith(OLD_FILE_PREFIX):
            index += 1
            continue
        old_path = _path_after(lines[index], OLD_FILE_PREFIX, OLD_PATH_PREFIX)
        if index + 1 >= len(lines) or not lines[index + 1].startswith(NEW_FILE_PREFIX):
            raise PatchFormatError("a file header without its new-file line")
        new_path = _path_after(lines[index + 1], NEW_FILE_PREFIX, NEW_PATH_PREFIX)
        index += 2
        removed: list[int] = []
        insertions: list[int] = []
        while index < len(lines) and lines[index].startswith("@@"):
            index = _read_hunk(lines, index, removed, insertions)
        changes.append(
            FileChange(
                file_path=new_path if old_path == MISSING_FILE else old_path,
                removed_lines=tuple(removed),
                insertion_points=tuple(insertions),
                is_new_file=old_path == MISSING_FILE,
            )
        )
    if not changes:
        raise PatchFormatError("no file changes found")
    return changes


def expected_changes(change: FileChange, source: bytes) -> list[ExpectedChange]:
    """Return the definitions at the base commit that a file's change touches, in file order."""
    if change.is_new_file:
        return []
    chunks = chunk_python_source(change.file_path, source)
    touched: dict[tuple[str, int], tuple[CodeChunk, CodeChunk, set[str], set[int]]] = {}
    for line in change.removed_lines:
        _record(touched, chunks, _smallest_chunk_holding(chunks, line), CHANGED_LINE_REASON, line)
    for point in change.insertion_points:
        chunk, line = _chunk_for_insertion(chunks, point)
        _record(touched, chunks, chunk, INSERTION_REASON, line)
    expected = [
        ExpectedChange(
            file_path=change.file_path,
            qualified_name=first.qualified_name,
            kind=str(first.kind),
            start_line=first.start_line,
            end_line=last.end_line,
            reasons=tuple(sorted(reasons)),
            lines=tuple(sorted(lines)),
        )
        for first, last, reasons, lines in touched.values()
    ]
    return sorted(expected, key=lambda item: item.start_line)


def split_module_level(
    changes: list[ExpectedChange],
) -> tuple[list[ExpectedChange], list[ExpectedChange]]:
    """Return the functions, methods, and classes changed, then the module-level code changed.

    When a fix changes nothing but module-level code, that code is all there is to find, so it
    stays expected.
    """
    definitions = [change for change in changes if change.kind != ChunkKind.MODULE]
    module_level = [change for change in changes if change.kind == ChunkKind.MODULE]
    if not definitions:
        return module_level, []
    return definitions, module_level


def _path_after(line: str, header_prefix: str, path_prefix: str) -> str:
    path = line[len(header_prefix) :].split("\t")[0].strip()
    if path == MISSING_FILE:
        return path
    if not path.startswith(path_prefix):
        raise PatchFormatError(f"a file path without the {path_prefix!r} prefix")
    return path[len(path_prefix) :]


def _read_hunk(lines: list[str], index: int, removed: list[int], insertions: list[int]) -> int:
    match = HUNK_HEADER_PATTERN.match(lines[index])
    if match is None:
        raise PatchFormatError("a hunk header that is not '@@ -a,b +c,d @@'")
    old_line = int(match.group(1))
    old_remaining = int(match.group(2)) if match.group(2) is not None else 1
    new_remaining = int(match.group(4)) if match.group(4) is not None else 1
    if old_remaining == 0:
        old_line += 1
    index += 1
    previous_kind = " "
    pending_insertion: int | None = None
    while index < len(lines) and (old_remaining > 0 or new_remaining > 0):
        text = lines[index]
        kind = text[:1]
        if kind == NO_NEWLINE_MARKER:
            index += 1
            continue
        if kind == "-":
            removed.append(old_line)
            old_line += 1
            old_remaining -= 1
            pending_insertion = None
        elif kind == "+":
            if previous_kind not in ("-", "+"):
                pending_insertion = old_line - 1
            new_remaining -= 1
        elif kind == " " or text == "":
            _close_insertion(pending_insertion, insertions)
            pending_insertion = None
            old_line += 1
            old_remaining -= 1
            new_remaining -= 1
        else:
            raise PatchFormatError("a hunk line that is not context, removed, or added")
        previous_kind = kind if kind in ("-", "+") else " "
        index += 1
    _close_insertion(pending_insertion, insertions)
    return index


def _close_insertion(pending_insertion: int | None, insertions: list[int]) -> None:
    if pending_insertion is not None:
        insertions.append(pending_insertion)


def _smallest_chunk_holding(chunks: list[CodeChunk], line: int) -> CodeChunk | None:
    holding = [chunk for chunk in chunks if chunk.start_line <= line <= chunk.end_line]
    if not holding:
        return None
    return min(holding, key=lambda chunk: chunk.end_line - chunk.start_line)


def _chunk_for_insertion(chunks: list[CodeChunk], point: int) -> tuple[CodeChunk | None, int]:
    before = _smallest_chunk_holding(chunks, point) if point >= 1 else None
    if before is not None and before.kind != ChunkKind.MODULE:
        return before, point
    after = _smallest_chunk_holding(chunks, point + 1)
    if after is not None and after.kind != ChunkKind.MODULE:
        return after, point + 1
    if before is not None:
        return before, point
    return after, point + 1


def _record(
    touched: dict[tuple[str, int], tuple[CodeChunk, CodeChunk, set[str], set[int]]],
    chunks: list[CodeChunk],
    chunk: CodeChunk | None,
    reason: str,
    line: int,
) -> None:
    if chunk is None:
        return
    first, last = _definition_parts(chunks, chunk)
    key = (first.qualified_name, first.start_line)
    if key not in touched:
        touched[key] = (first, last, set(), set())
    touched[key][2].add(reason)
    touched[key][3].add(line)


def _definition_parts(chunks: list[CodeChunk], chunk: CodeChunk) -> tuple[CodeChunk, CodeChunk]:
    same_definition_kind = [
        candidate
        for candidate in chunks
        if candidate.qualified_name == chunk.qualified_name
        and candidate.kind == chunk.kind
        and candidate.part_count == chunk.part_count
    ]
    for group in _part_groups(same_definition_kind):
        if any(part is chunk for part in group):
            return group[0], group[-1]
    raise PatchFormatError(f"the parts of {chunk.qualified_name} could not be found")


def _part_groups(chunks: list[CodeChunk]) -> list[list[CodeChunk]]:
    groups: list[list[CodeChunk]] = []
    for chunk in chunks:
        if chunk.part_number == 1:
            groups.append([chunk])
        elif groups:
            groups[-1].append(chunk)
    return groups
