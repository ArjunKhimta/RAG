"""Split one Python file into search chunks.

Each top-level function becomes one chunk. Each class becomes an outline chunk, in which method
bodies are replaced with `...`, plus one chunk per method. Top-level statements that are not
definitions, such as imports and constants, become module chunks, one per unbroken run.

Decorators and comments directly above a definition, with no blank line between, belong to that
definition's chunk. Nested functions stay inside the function that defines them.

A chunk's text is exactly the source lines in its range. Class outlines are the one exception,
because their method bodies are replaced.

A chunk longer than `MAX_CHUNK_CHARACTERS` is split between statements into numbered parts, and a
single statement that is too long on its own is split between lines. Parts after the first carry
the definition's signature in `signature` rather than in their text, so the embedding step can add
the context those parts lack.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from tree_sitter import Node

from retrieval.parsing import find_line_start_bytes, line_range_of, parse_python_source

MAX_CHUNK_CHARACTERS = 4000

SOURCE_ENCODING = "utf-8"

MODULE_CHUNK_NAME = "<module>"

UNNAMED_DEFINITION = "<unnamed>"

OUTLINE_BODY_PLACEHOLDER = b"..."

FUNCTION_NODE_TYPE = "function_definition"

CLASS_NODE_TYPE = "class_definition"

DECORATED_NODE_TYPE = "decorated_definition"

COMMENT_NODE_TYPE = "comment"

HEADER_END_NODE_TYPE = ":"

DEFINITION_NODE_TYPES = frozenset({FUNCTION_NODE_TYPE, CLASS_NODE_TYPE})


class ChunkKind(StrEnum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    MODULE = "module"


@dataclass(frozen=True)
class CodeChunk:
    """One searchable piece of a file, with the location a citation needs.

    `start_line` and `end_line` are 1-indexed and inclusive. `signature` is set only on the second
    and later parts of a split definition, whose text does not include the definition's header.
    `is_test_file` and `contains_redaction` are set by the repository walker, which knows the
    file's place in the repository and which of its lines had secrets redacted. `calls` holds the
    symbols of the definitions this chunk calls, set once the whole repository's call graph is
    built.
    """

    file_path: str
    start_line: int
    end_line: int
    kind: ChunkKind
    name: str
    qualified_name: str
    parent_class: str | None
    text: str
    signature: str | None = None
    part_number: int = 1
    part_count: int = 1
    is_test_file: bool = False
    contains_redaction: bool = False
    calls: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Definition:
    node: Node
    first_line: int
    last_line: int


@dataclass(frozen=True)
class _LeftoverRun:
    nodes: tuple[Node, ...]


@dataclass(frozen=True)
class _Segment:
    first_line: int
    last_line: int
    text: str


def chunk_python_source(file_path: str, source: bytes) -> list[CodeChunk]:
    """Return the chunks of one Python file in file order. `file_path` is repo-relative."""
    tree = parse_python_source(source)
    file_chunker = _FileChunker(file_path, source)
    return file_chunker.chunk_module(tree.root_node)


class _SourceLines:
    """Byte-level access to a file by 1-indexed line number."""

    def __init__(self, source: bytes) -> None:
        self._source = source
        self._line_start_bytes = find_line_start_bytes(source)

    def text_of(self, first_line: int, last_line: int) -> str:
        start_byte, end_byte = self.byte_range_of(first_line, last_line)
        return _decode(self._source[start_byte:end_byte])

    def byte_range_of(self, first_line: int, last_line: int) -> tuple[int, int]:
        """Return the bytes from the start of `first_line` to the end of `last_line`.

        The newline that ends `last_line` is not included.
        """
        start_byte = self._line_start_byte(first_line)
        end_byte = self._line_end_byte(last_line)
        return start_byte, end_byte

    def is_first_on_its_line(self, node: Node) -> bool:
        line = line_range_of(node).first_line
        text_before_node = self._source[self._line_start_byte(line) : node.start_byte]
        return text_before_node.strip() == b""

    def _line_start_byte(self, line: int) -> int:
        return self._line_start_bytes[line - 1]

    def _line_end_byte(self, line: int) -> int:
        line_count = len(self._line_start_bytes)
        if line == line_count:
            return len(self._source)
        newline_byte = self._line_start_byte(line + 1) - 1
        return newline_byte


class _FileChunker:
    def __init__(self, file_path: str, source: bytes) -> None:
        self._file_path = file_path
        self._source = source
        self._lines = _SourceLines(source)

    def chunk_module(self, module_node: Node) -> list[CodeChunk]:
        chunks: list[CodeChunk] = []
        for group in self._group_definitions(module_node.named_children):
            if isinstance(group, _Definition):
                chunks.extend(self._chunk_definition(group, parent_class=None))
            else:
                chunks.extend(self._chunk_leftover_run(group))
        return chunks

    def _group_definitions(self, items: list[Node]) -> list[_Definition | _LeftoverRun]:
        """Split sibling statements into definitions and the runs of other statements between them.

        Comments directly above a definition move out of the preceding run and into the
        definition, whose first line moves up to the first of those comments.
        """
        groups: list[_Definition | _LeftoverRun] = []
        pending_items: list[Node] = []
        for item in items:
            definition_node = definition_inside(item)
            if definition_node is None:
                pending_items.append(item)
                continue
            item_lines = line_range_of(item)
            attached_count = self._count_attached_comments(pending_items, item_lines.first_line)
            leftover_count = len(pending_items) - attached_count
            leftover_items = pending_items[:leftover_count]
            attached_comments = pending_items[leftover_count:]
            if leftover_items:
                groups.append(_LeftoverRun(tuple(leftover_items)))
            first_line = item_lines.first_line
            if attached_comments:
                first_line = line_range_of(attached_comments[0]).first_line
            groups.append(_Definition(definition_node, first_line, item_lines.last_line))
            pending_items = []
        if pending_items:
            groups.append(_LeftoverRun(tuple(pending_items)))
        return groups

    def _count_attached_comments(
        self, pending_items: list[Node], definition_first_line: int
    ) -> int:
        attached_count = 0
        next_first_line = definition_first_line
        for item in reversed(pending_items):
            if not self._is_leading_comment(item, next_first_line):
                break
            attached_count += 1
            next_first_line = line_range_of(item).first_line
        return attached_count

    def _is_leading_comment(self, item: Node, next_first_line: int) -> bool:
        if item.type != COMMENT_NODE_TYPE:
            return False
        touches_next_line = line_range_of(item).last_line == next_first_line - 1
        return touches_next_line and self._lines.is_first_on_its_line(item)

    def _chunk_definition(
        self, definition: _Definition, parent_class: str | None
    ) -> list[CodeChunk]:
        if definition.node.type == CLASS_NODE_TYPE:
            return self._chunk_class(definition, parent_class)
        return self._chunk_function(definition, parent_class)

    def _chunk_function(self, definition: _Definition, parent_class: str | None) -> list[CodeChunk]:
        function_node = definition.node
        kind = ChunkKind.FUNCTION if parent_class is None else ChunkKind.METHOD
        boundary_lines = _first_lines_of(body_items(function_node))
        segments = self._raw_segments_between(
            definition.first_line, definition.last_line, boundary_lines
        )
        return self._build_chunks(
            segments,
            kind=kind,
            name=self._read_name(function_node),
            parent_class=parent_class,
            signature=self._signature_of(function_node),
        )

    def _chunk_class(self, definition: _Definition, parent_class: str | None) -> list[CodeChunk]:
        class_node = definition.node
        name = self._read_name(class_node)
        members = self._member_definitions(class_node)
        outline_segments = self._outline_segments(definition, members)
        chunks = self._build_chunks(
            outline_segments,
            kind=ChunkKind.CLASS,
            name=name,
            parent_class=parent_class,
            signature=self._signature_of(class_node),
        )
        qualified_name = qualify(name, parent_class)
        for member in members:
            chunks.extend(self._chunk_definition(member, parent_class=qualified_name))
        return chunks

    def _chunk_leftover_run(self, run: _LeftoverRun) -> list[CodeChunk]:
        first_line = line_range_of(run.nodes[0]).first_line
        last_line = line_range_of(run.nodes[-1]).last_line
        boundary_lines = _first_lines_of(list(run.nodes))
        segments = self._raw_segments_between(first_line, last_line, boundary_lines)
        return self._build_chunks(
            segments,
            kind=ChunkKind.MODULE,
            name=MODULE_CHUNK_NAME,
            parent_class=None,
            signature=None,
        )

    def _member_definitions(self, class_node: Node) -> list[_Definition]:
        groups = self._group_definitions(body_items(class_node))
        return [group for group in groups if isinstance(group, _Definition)]

    def _outline_segments(
        self, definition: _Definition, members: list[_Definition]
    ) -> list[_Segment]:
        replaced_bodies = self._method_bodies(members)
        boundary_lines = _first_lines_of(body_items(definition.node))
        units = _units_between(definition.first_line, definition.last_line, boundary_lines)
        segments: list[_Segment] = []
        for first_line, last_line in units:
            segments.extend(self._outline_segments_of_unit(first_line, last_line, replaced_bodies))
        return segments

    def _method_bodies(self, members: list[_Definition]) -> list[Node]:
        """Return the bodies to hide in an outline, including those of methods in nested classes."""
        bodies: list[Node] = []
        for member in members:
            if member.node.type == CLASS_NODE_TYPE:
                nested_members = self._member_definitions(member.node)
                bodies.extend(self._method_bodies(nested_members))
                continue
            body = member.node.child_by_field_name("body")
            if body is not None:
                bodies.append(body)
        return bodies

    def _outline_segments_of_unit(
        self, first_line: int, last_line: int, replaced_bodies: list[Node]
    ) -> list[_Segment]:
        start_byte, end_byte = self._lines.byte_range_of(first_line, last_line)
        bodies_in_unit = [
            body
            for body in replaced_bodies
            if start_byte <= body.start_byte and body.end_byte <= end_byte
        ]
        if not bodies_in_unit:
            return self._raw_segments(first_line, last_line)
        outlined_text = self._replace_bodies(start_byte, end_byte, bodies_in_unit)
        return [_Segment(first_line, last_line, outlined_text)]

    def _replace_bodies(self, start_byte: int, end_byte: int, bodies: list[Node]) -> str:
        pieces: list[bytes] = []
        cursor = start_byte
        for body in bodies:
            pieces.append(self._source[cursor : body.start_byte])
            pieces.append(OUTLINE_BODY_PLACEHOLDER)
            cursor = body.end_byte
        pieces.append(self._source[cursor:end_byte])
        return _decode(b"".join(pieces))

    def _raw_segments_between(
        self, first_line: int, last_line: int, boundary_lines: list[int]
    ) -> list[_Segment]:
        segments: list[_Segment] = []
        units = _units_between(first_line, last_line, boundary_lines)
        for unit_first_line, unit_last_line in units:
            segments.extend(self._raw_segments(unit_first_line, unit_last_line))
        return segments

    def _raw_segments(self, first_line: int, last_line: int) -> list[_Segment]:
        """Return one segment for the lines, or one per line when they are too long together."""
        text = self._lines.text_of(first_line, last_line)
        if len(text) <= MAX_CHUNK_CHARACTERS:
            return [_Segment(first_line, last_line, text)]
        return [
            _Segment(line, line, self._lines.text_of(line, line))
            for line in range(first_line, last_line + 1)
        ]

    def _build_chunks(
        self,
        segments: list[_Segment],
        kind: ChunkKind,
        name: str,
        parent_class: str | None,
        signature: str | None,
    ) -> list[CodeChunk]:
        groups = _pack_segments(segments)
        chunks: list[CodeChunk] = []
        for part_index, group in enumerate(groups):
            part_number = part_index + 1
            is_first_part = part_number == 1
            chunk = CodeChunk(
                file_path=self._file_path,
                start_line=group[0].first_line,
                end_line=group[-1].last_line,
                kind=kind,
                name=name,
                qualified_name=qualify(name, parent_class),
                parent_class=parent_class,
                text="\n".join(segment.text for segment in group),
                signature=None if is_first_part else signature,
                part_number=part_number,
                part_count=len(groups),
            )
            chunks.append(chunk)
        return chunks

    def _signature_of(self, definition_node: Node) -> str | None:
        """Return the header from `def` or `class` through its colon, without decorators."""
        header_end = _first_child_of_type(definition_node, HEADER_END_NODE_TYPE)
        if header_end is None:
            return None
        return _decode(self._source[definition_node.start_byte : header_end.end_byte])

    def _read_name(self, definition_node: Node) -> str:
        name_node = definition_node.child_by_field_name("name")
        if name_node is None:
            return UNNAMED_DEFINITION
        return _decode(self._source[name_node.start_byte : name_node.end_byte])


def definition_inside(item: Node) -> Node | None:
    """Return the function or class that `item` is or wraps with decorators, or None."""
    candidate = item
    if item.type == DECORATED_NODE_TYPE:
        candidate = item.child_by_field_name("definition")
    if candidate is None or candidate.type not in DEFINITION_NODE_TYPES:
        return None
    return candidate


def body_items(definition_node: Node) -> list[Node]:
    """Return the statements and comments inside a definition's body, in source order.

    Tree-sitter attaches a comment that comes before the first statement of a body to the
    definition itself rather than to the body block, so both places are collected.
    """
    header_comments = [
        child for child in definition_node.named_children if child.type == COMMENT_NODE_TYPE
    ]
    body = definition_node.child_by_field_name("body")
    body_statements = [] if body is None else body.named_children
    return sorted([*header_comments, *body_statements], key=_start_byte_of)


def _units_between(
    first_line: int, last_line: int, boundary_lines: list[int]
) -> list[tuple[int, int]]:
    """Cut `first_line` through `last_line` into contiguous units, one starting at each boundary."""
    inner_start_lines = sorted({line for line in boundary_lines if first_line < line <= last_line})
    unit_first_lines = [first_line, *inner_start_lines]
    unit_last_lines = [start_line - 1 for start_line in inner_start_lines] + [last_line]
    return list(zip(unit_first_lines, unit_last_lines, strict=True))


def _pack_segments(segments: list[_Segment]) -> list[list[_Segment]]:
    """Group consecutive segments greedily so each group's joined text fits the size limit."""
    groups: list[list[_Segment]] = []
    current_group: list[_Segment] = []
    for segment in segments:
        candidate_group = [*current_group, segment]
        if current_group and _joined_length(candidate_group) > MAX_CHUNK_CHARACTERS:
            groups.append(current_group)
            current_group = [segment]
        else:
            current_group = candidate_group
    if current_group:
        groups.append(current_group)
    return groups


def _joined_length(segments: list[_Segment]) -> int:
    text_length = sum(len(segment.text) for segment in segments)
    newline_count = len(segments) - 1
    return text_length + newline_count


def _first_lines_of(nodes: list[Node]) -> list[int]:
    return [line_range_of(node).first_line for node in nodes]


def _first_child_of_type(node: Node, node_type: str) -> Node | None:
    for child in node.children:
        if child.type == node_type:
            return child
    return None


def _start_byte_of(node: Node) -> int:
    return node.start_byte


def qualify(name: str, parent_class: str | None) -> str:
    if parent_class is None:
        return name
    return f"{parent_class}.{name}"


def _decode(raw: bytes) -> str:
    return raw.decode(SOURCE_ENCODING, errors="replace")
