"""Find secrets in a source file and redact them before the file is chunked.

Redaction happens on the whole file, before chunking, so every chunk built from it is clean,
including class outlines, and nothing downstream (hashing, embedding, storage, display) ever holds
a secret.

Only string contents and comments are ever changed. The file is parsed with Tree-sitter, and a
secret is replaced only where it sits inside a string or a comment on its flagged line. Code is
never touched, so a secret whose value is also a name in the code, as in
`app.secret_key = "secret_key"`, cannot break the file's structure. The placeholder contains no
line break, so line numbers and citations are unchanged too.

Detection uses `detect-secrets` with its default rules: known key formats, a keyword rule for
names like `password` and `secret_key`, and randomness thresholds. Every finding is redacted,
including obvious placeholders such as `SECRET_KEY = "dev"`, because a hidden placeholder costs
far less than a leaked credential. This is the only module that imports `detect-secrets`.

The library fails open: it silently reports nothing for a file it cannot decode. This wrapper
fails closed instead, raising `UnscannableSourceError` for a file that is not valid UTF-8, whose
line breaks the library would count differently, or with a finding that cannot be redacted inside
a string or comment. Such a file must not be indexed.

`detect-secrets` keeps its settings in process-wide state, so scans must not run concurrently.
"""

from __future__ import annotations

import bisect
import locale
import re
from dataclasses import dataclass
from pathlib import Path

from detect_secrets.core.potential_secret import PotentialSecret
from detect_secrets.core.scan import scan_file
from detect_secrets.settings import default_settings

from retrieval.parsing import find_line_start_bytes, parse_python_source
from retrieval.redaction import REDACTION_PLACEHOLDER

SOURCE_ENCODING = "utf-8"

UTF8_ENCODING_NAMES = frozenset({"utf-8", "utf8"})

REDACTION_PLACEHOLDER_BYTES = REDACTION_PLACEHOLDER.encode(SOURCE_ENCODING)

STRING_CONTENT_NODE_TYPE = "string_content"

COMMENT_NODE_TYPE = "comment"

COMMENT_MARKER_LENGTH = len("#")

PRIVATE_KEY_SECRET_TYPE = "Private Key"

PRIVATE_KEY_BEGIN_PATTERN = re.compile(rb"-----BEGIN[A-Z0-9 ]* PRIVATE KEY[A-Z ]*-----")

PRIVATE_KEY_END_PATTERN = re.compile(rb"-----END[A-Z0-9 ]* PRIVATE KEY[A-Z ]*-----")

NEWLINE = b"\n"

CARRIAGE_RETURN = b"\r"

WINDOWS_LINE_ENDING = b"\r\n"

INDENTATION_CHARACTERS = (b" ", b"\t")

TOKEN_CHARACTER_CODES = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-+/=.~"
)


class UnscannableSourceError(RuntimeError):
    """Raised when a file cannot be scanned or redacted reliably, so it must not be indexed."""


@dataclass(frozen=True)
class SecretFinding:
    """Where a secret was found and what kind it was. The value itself is never kept."""

    file_path: str
    line_number: int
    secret_type: str


@dataclass(frozen=True)
class RedactedSource:
    source: bytes
    findings: list[SecretFinding]
    redacted_line_numbers: frozenset[int]


@dataclass(frozen=True)
class _ByteSpan:
    start: int
    end: int


def scan_and_redact(file_path: Path, relative_path: str, source: bytes) -> RedactedSource:
    """Return `source` with every detected secret replaced by the redaction placeholder.

    `file_path` is the file on disk that holds exactly `source`; `detect-secrets` reads it from
    there. Findings are reported under `relative_path`, with 1-indexed line numbers.
    """
    _require_valid_utf8(relative_path, source)
    _require_line_breaks_counted_alike(relative_path, source)
    _require_utf8_default_encoding(relative_path)
    with default_settings():
        potential_secrets = list(scan_file(str(file_path)))
    if not potential_secrets:
        return RedactedSource(source=source, findings=[], redacted_line_numbers=frozenset())
    line_start_bytes = find_line_start_bytes(source)
    redactable_spans = _string_and_comment_spans(source)
    findings: list[SecretFinding] = []
    spans_to_replace: list[_ByteSpan] = []
    for potential_secret in potential_secrets:
        finding = SecretFinding(relative_path, potential_secret.line_number, potential_secret.type)
        secret_spans = _spans_for_secret(
            source, line_start_bytes, redactable_spans, potential_secret
        )
        if not secret_spans:
            message = (
                f"{relative_path}:{finding.line_number} has a {finding.secret_type} "
                "outside any string or comment, so it cannot be redacted safely"
            )
            raise UnscannableSourceError(message)
        findings.append(finding)
        spans_to_replace.extend(secret_spans)
    merged_spans = _merge_overlapping_spans(spans_to_replace)
    return RedactedSource(
        source=_replace_spans(source, merged_spans),
        findings=sorted(findings, key=_finding_order),
        redacted_line_numbers=_line_numbers_of(merged_spans, line_start_bytes),
    )


def _spans_for_secret(
    source: bytes,
    line_start_bytes: list[int],
    redactable_spans: list[_ByteSpan],
    potential_secret: PotentialSecret,
) -> list[_ByteSpan]:
    line_number = potential_secret.line_number
    if not 1 <= line_number <= len(line_start_bytes):
        return []
    line_span = _line_span(source, line_start_bytes, line_number)
    if potential_secret.type == PRIVATE_KEY_SECRET_TYPE:
        return _private_key_spans(source, line_span, redactable_spans)
    return _value_spans(source, line_span, potential_secret.secret_value, redactable_spans)


def _value_spans(
    source: bytes,
    line_span: _ByteSpan,
    secret_value: str | None,
    redactable_spans: list[_ByteSpan],
) -> list[_ByteSpan]:
    """Find every copy of the value inside a string or comment on the flagged line.

    The library reports the value but not its position, so every such copy is replaced. Copies in
    code and on other lines are left alone: a short value such as `dev` also occurs in names.
    Some rules report only the start of a token (the GitHub rule reports just `ghp`), so each copy
    is widened to the whole token around it, without leaving its string or comment.
    """
    if not secret_value:
        return []
    value_bytes = secret_value.encode(SOURCE_ENCODING)
    value_spans: list[_ByteSpan] = []
    for search_span in _intersections(line_span, redactable_spans):
        position = source.find(value_bytes, search_span.start, search_span.end)
        while position != -1:
            copy_span = _ByteSpan(position, position + len(value_bytes))
            token_span = _widen_to_token(source, copy_span, search_span)
            value_spans.append(token_span)
            position = source.find(value_bytes, token_span.end, search_span.end)
    return value_spans


def _widen_to_token(source: bytes, span: _ByteSpan, bounds: _ByteSpan) -> _ByteSpan:
    """Extend a span over adjacent token characters: letters, digits, and `_-+/=.~`.

    Quotes, spaces, `:`, and `@` stop it, so a password inside `user:password@host` stays apart.
    """
    start = span.start
    end = span.end
    while start > bounds.start and source[start - 1] in TOKEN_CHARACTER_CODES:
        start -= 1
    while end < bounds.end and source[end] in TOKEN_CHARACTER_CODES:
        end += 1
    return _ByteSpan(start, end)


def _private_key_spans(
    source: bytes, line_span: _ByteSpan, redactable_spans: list[_ByteSpan]
) -> list[_ByteSpan]:
    """Cover the key from its BEGIN marker through its END marker, which may be lines later.

    The library flags only the BEGIN line, and the key material sits on the lines after it.
    Without an END marker the block runs to the end of the file. Only the parts of the block
    inside strings and comments are replaced, one span per line, so line breaks survive.
    """
    begin_match = PRIVATE_KEY_BEGIN_PATTERN.search(source, line_span.start, line_span.end)
    if begin_match is None:
        return []
    end_match = PRIVATE_KEY_END_PATTERN.search(source, begin_match.end())
    block_end = len(source) if end_match is None else end_match.end()
    block_span = _ByteSpan(begin_match.start(), block_end)
    key_spans: list[_ByteSpan] = []
    for covered_span in _intersections(block_span, redactable_spans):
        key_spans.extend(_split_into_line_pieces(source, covered_span))
    return key_spans


def _string_and_comment_spans(source: bytes) -> list[_ByteSpan]:
    """Return the byte ranges of string contents and comment text, where redaction is allowed.

    A comment's `#` is kept so that a redacted comment is still a comment.
    """
    tree = parse_python_source(source)
    redactable_spans: list[_ByteSpan] = []
    pending_nodes = [tree.root_node]
    while pending_nodes:
        node = pending_nodes.pop()
        if node.type == STRING_CONTENT_NODE_TYPE:
            redactable_spans.append(_ByteSpan(node.start_byte, node.end_byte))
        elif node.type == COMMENT_NODE_TYPE:
            comment_text_start = node.start_byte + COMMENT_MARKER_LENGTH
            redactable_spans.append(_ByteSpan(comment_text_start, node.end_byte))
        pending_nodes.extend(node.children)
    return sorted(redactable_spans, key=_span_start)


def _intersections(span: _ByteSpan, other_spans: list[_ByteSpan]) -> list[_ByteSpan]:
    intersections: list[_ByteSpan] = []
    for other_span in other_spans:
        start = max(span.start, other_span.start)
        end = min(span.end, other_span.end)
        if start < end:
            intersections.append(_ByteSpan(start, end))
    return intersections


def _split_into_line_pieces(source: bytes, span: _ByteSpan) -> list[_ByteSpan]:
    """Split a span at line breaks, dropping each piece's indentation and line ending."""
    pieces: list[_ByteSpan] = []
    piece_start = span.start
    while piece_start < span.end:
        newline_position = source.find(NEWLINE, piece_start, span.end)
        piece_end = span.end if newline_position == -1 else newline_position
        trimmed_piece = _trim_indentation_and_line_ending(source, _ByteSpan(piece_start, piece_end))
        if trimmed_piece is not None:
            pieces.append(trimmed_piece)
        piece_start = piece_end + len(NEWLINE)
    return pieces


def _trim_indentation_and_line_ending(source: bytes, piece: _ByteSpan) -> _ByteSpan | None:
    start = piece.start
    end = piece.end
    while start < end and source[start : start + 1] in INDENTATION_CHARACTERS:
        start += 1
    if start < end and source[end - 1 : end] == CARRIAGE_RETURN:
        end -= 1
    if start >= end:
        return None
    return _ByteSpan(start, end)


def _merge_overlapping_spans(spans: list[_ByteSpan]) -> list[_ByteSpan]:
    merged_spans: list[_ByteSpan] = []
    for span in sorted(spans, key=_span_start):
        if merged_spans and span.start < merged_spans[-1].end:
            previous_span = merged_spans.pop()
            span = _ByteSpan(previous_span.start, max(previous_span.end, span.end))
        merged_spans.append(span)
    return merged_spans


def _replace_spans(source: bytes, spans: list[_ByteSpan]) -> bytes:
    pieces: list[bytes] = []
    cursor = 0
    for span in spans:
        pieces.append(source[cursor : span.start])
        pieces.append(REDACTION_PLACEHOLDER_BYTES)
        cursor = span.end
    pieces.append(source[cursor:])
    return b"".join(pieces)


def _line_span(source: bytes, line_start_bytes: list[int], line_number: int) -> _ByteSpan:
    """Return the bytes of a 1-indexed line, without its newline."""
    start = line_start_bytes[line_number - 1]
    is_last_line = line_number == len(line_start_bytes)
    end = len(source) if is_last_line else line_start_bytes[line_number] - len(NEWLINE)
    return _ByteSpan(start, end)


def _line_numbers_of(spans: list[_ByteSpan], line_start_bytes: list[int]) -> frozenset[int]:
    """Return the 1-indexed lines the spans touch; the count of line starts at or before an offset
    is that offset's line number."""
    line_numbers: set[int] = set()
    for span in spans:
        first_line = bisect.bisect_right(line_start_bytes, span.start)
        last_line = bisect.bisect_right(line_start_bytes, span.end - 1)
        line_numbers.update(range(first_line, last_line + 1))
    return frozenset(line_numbers)


def _require_valid_utf8(relative_path: str, source: bytes) -> None:
    try:
        source.decode(SOURCE_ENCODING)
    except UnicodeDecodeError as error:
        message = f"{relative_path} is not valid UTF-8, so it cannot be scanned for secrets"
        raise UnscannableSourceError(message) from error


def _require_line_breaks_counted_alike(relative_path: str, source: bytes) -> None:
    """Refuse a lone carriage return, which the library counts as a line break but Tree-sitter
    does not, so reported line numbers would point at the wrong lines."""
    source_without_windows_endings = source.replace(WINDOWS_LINE_ENDING, b"")
    if CARRIAGE_RETURN in source_without_windows_endings:
        message = f"{relative_path} has a carriage return without a line feed"
        raise UnscannableSourceError(message)


def _require_utf8_default_encoding(relative_path: str) -> None:
    """The library opens files with Python's default encoding and skips what it cannot decode."""
    default_encoding = locale.getpreferredencoding(False).lower()
    if default_encoding not in UTF8_ENCODING_NAMES:
        message = (
            f"{relative_path} cannot be scanned: Python's default encoding is {default_encoding}, "
            "not UTF-8"
        )
        raise UnscannableSourceError(message)


def _span_start(span: _ByteSpan) -> int:
    return span.start


def _finding_order(finding: SecretFinding) -> tuple[int, str]:
    return (finding.line_number, finding.secret_type)
