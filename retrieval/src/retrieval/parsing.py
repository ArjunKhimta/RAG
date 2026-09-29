"""Tree-sitter setup for Python source, shared by the chunker and the tests.

Tree-sitter numbers rows from 0, while every line number shown to users starts at 1.
`line_range_of` is the one place that converts between the two.
"""

from __future__ import annotations

from dataclasses import dataclass

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

PYTHON_LANGUAGE = Language(tree_sitter_python.language())


@dataclass(frozen=True)
class LineRange:
    """A 1-indexed, inclusive span of source lines."""

    first_line: int
    last_line: int


def parse_python_source(source: bytes) -> Tree:
    """Parse `source` with a fresh Parser, because Parser objects are not thread-safe."""
    parser = Parser(PYTHON_LANGUAGE)
    return parser.parse(source)


def line_range_of(node: Node) -> LineRange:
    """Return the 1-indexed, inclusive lines that `node` occupies.

    A node that ends at column 0 of a later row stops just after a newline, so that row holds none
    of its text and is not counted.
    """
    first_line = node.start_point.row + 1
    last_line = node.end_point.row + 1
    ends_at_column_zero = node.end_point.column == 0
    ends_on_a_later_row = node.end_point.row > node.start_point.row
    if ends_at_column_zero and ends_on_a_later_row:
        last_line = node.end_point.row
    return LineRange(first_line=first_line, last_line=last_line)


def find_line_start_bytes(source: bytes) -> list[int]:
    """Return the byte offset where each line starts, splitting on newlines as Tree-sitter does."""
    line_start_bytes = [0]
    newline_byte = source.find(b"\n")
    while newline_byte != -1:
        line_start_bytes.append(newline_byte + 1)
        newline_byte = source.find(b"\n", newline_byte + 1)
    return line_start_bytes
