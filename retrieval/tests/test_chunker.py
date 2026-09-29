from __future__ import annotations

from retrieval.chunker import MAX_CHUNK_CHARACTERS, ChunkKind, CodeChunk, chunk_python_source

SAMPLE_FILE_PATH = "pkg/sample.py"

TOP_LEVEL_FUNCTION_SOURCE = """\
def load_settings(path):
    return {"path": path}
"""

CLASS_SOURCE = """\
class Store(Base):
    \"\"\"Keeps items.\"\"\"

    limit = 3

    def size(self):
        return len(self.items)

    @property
    def is_full(self) -> bool:
        return self.size() >= self.limit
"""

NESTED_CLASS_SOURCE = """\
class Outer:
    class Inner:
        def ping(self):
            return "pong"
"""

DECORATED_SOURCE = """\
import functools


@functools.cache
@trace
def cached_value():
    return 42


@dataclass
class Point:
    x: int
"""

LEADING_COMMENT_SOURCE = """\
import os

# Loads the settings file.
# Falls back to defaults.
def load():
    return os.environ


# Not attached: a blank line separates it.

def save():
    pass
value = 1  # trailing comment on another statement
def export():
    pass


class Store:
    # Comment above the first method.
    def size(self):
        return 0

    # Comment above a decorated method.
    @property
    def empty(self):
        return True
"""

NESTED_FUNCTION_SOURCE = """\
def route(rule):
    def decorator(function):
        return function

    return decorator
"""

ASYNC_SOURCE = """\
async def fetch(url):
    return await get(url)
"""

LEFTOVER_SOURCE = """\
\"\"\"Module docstring.\"\"\"
import os

DEFAULT_PORT = 5000


def main():
    return DEFAULT_PORT


if __name__ == "__main__":
    main()
"""

NON_ASCII_SOURCE = """\
def greet():
    return "café ☕"


def after():
    return 2
"""

SYNTAX_ERROR_SOURCE = """\
def good():
    return 1


def broken(:
    pass


def also_good():
    return 2
"""


def test_a_top_level_function_becomes_one_chunk_with_1_indexed_lines():
    chunks = _chunk(TOP_LEVEL_FUNCTION_SOURCE)

    assert chunks == [
        CodeChunk(
            file_path=SAMPLE_FILE_PATH,
            start_line=1,
            end_line=2,
            kind=ChunkKind.FUNCTION,
            name="load_settings",
            qualified_name="load_settings",
            parent_class=None,
            text='def load_settings(path):\n    return {"path": path}',
        )
    ]


def test_a_class_becomes_an_outline_chunk_plus_one_chunk_per_method():
    chunks = _chunk(CLASS_SOURCE)

    assert [(chunk.kind, chunk.qualified_name) for chunk in chunks] == [
        (ChunkKind.CLASS, "Store"),
        (ChunkKind.METHOD, "Store.size"),
        (ChunkKind.METHOD, "Store.is_full"),
    ]
    method_chunks = chunks[1:]
    assert all(chunk.parent_class == "Store" for chunk in method_chunks)


def test_the_class_outline_covers_the_class_and_hides_method_bodies():
    outline_chunk = _chunk(CLASS_SOURCE)[0]

    assert (outline_chunk.start_line, outline_chunk.end_line) == (1, 11)
    assert outline_chunk.text == (
        "class Store(Base):\n"
        '    """Keeps items."""\n'
        "\n"
        "    limit = 3\n"
        "\n"
        "    def size(self):\n"
        "        ...\n"
        "\n"
        "    @property\n"
        "    def is_full(self) -> bool:\n"
        "        ..."
    )


def test_method_chunks_keep_their_indentation_and_exact_lines():
    size_chunk = _chunk_named(_chunk(CLASS_SOURCE), "Store.size")

    assert (size_chunk.start_line, size_chunk.end_line) == (6, 7)
    assert size_chunk.text == "    def size(self):\n        return len(self.items)"


def test_nested_classes_are_qualified_by_their_enclosing_class():
    chunks = _chunk(NESTED_CLASS_SOURCE)

    assert [(chunk.kind, chunk.qualified_name, chunk.parent_class) for chunk in chunks] == [
        (ChunkKind.CLASS, "Outer", None),
        (ChunkKind.CLASS, "Outer.Inner", "Outer"),
        (ChunkKind.METHOD, "Outer.Inner.ping", "Outer.Inner"),
    ]
    assert 'return "pong"' not in chunks[0].text


def test_a_decorated_function_starts_at_its_first_decorator():
    function_chunk = _chunk_named(_chunk(DECORATED_SOURCE), "cached_value")

    assert (function_chunk.start_line, function_chunk.end_line) == (4, 7)
    assert function_chunk.text.startswith("@functools.cache\n@trace\ndef cached_value():")


def test_a_decorated_class_starts_at_its_decorator():
    class_chunk = _chunk_named(_chunk(DECORATED_SOURCE), "Point")

    assert class_chunk.kind == ChunkKind.CLASS
    assert (class_chunk.start_line, class_chunk.end_line) == (10, 12)


def test_a_decorated_method_starts_at_its_decorator():
    method_chunk = _chunk_named(_chunk(CLASS_SOURCE), "Store.is_full")

    assert (method_chunk.start_line, method_chunk.end_line) == (9, 11)
    assert method_chunk.text.startswith("    @property\n")


def test_comments_directly_above_a_function_move_its_start_line_up():
    load_chunk = _chunk_named(_chunk(LEADING_COMMENT_SOURCE), "load")

    assert (load_chunk.start_line, load_chunk.end_line) == (3, 6)
    assert load_chunk.text.startswith("# Loads the settings file.\n# Falls back to defaults.\n")


def test_a_comment_separated_by_a_blank_line_stays_in_module_code():
    chunks = _chunk(LEADING_COMMENT_SOURCE)
    save_chunk = _chunk_named(chunks, "save")
    module_texts = [chunk.text for chunk in chunks if chunk.kind == ChunkKind.MODULE]

    assert save_chunk.start_line == 11
    assert "# Not attached: a blank line separates it." in module_texts


def test_a_trailing_comment_on_another_statement_is_not_attached():
    export_chunk = _chunk_named(_chunk(LEADING_COMMENT_SOURCE), "export")

    assert export_chunk.start_line == 14
    assert export_chunk.text.startswith("def export():")


def test_a_comment_above_the_first_method_of_a_class_is_attached():
    size_chunk = _chunk_named(_chunk(LEADING_COMMENT_SOURCE), "Store.size")

    assert (size_chunk.start_line, size_chunk.end_line) == (19, 21)
    assert size_chunk.text.startswith("    # Comment above the first method.\n")


def test_a_comment_above_decorators_is_attached_to_the_decorated_method():
    empty_chunk = _chunk_named(_chunk(LEADING_COMMENT_SOURCE), "Store.empty")

    assert (empty_chunk.start_line, empty_chunk.end_line) == (23, 26)
    assert empty_chunk.text.startswith("    # Comment above a decorated method.\n    @property\n")


def test_a_nested_function_stays_inside_its_enclosing_function():
    chunks = _chunk(NESTED_FUNCTION_SOURCE)

    assert len(chunks) == 1
    assert chunks[0].name == "route"
    assert "def decorator(function):" in chunks[0].text


def test_an_async_function_is_chunked_like_any_other_function():
    chunks = _chunk(ASYNC_SOURCE)

    assert [(chunk.kind, chunk.name) for chunk in chunks] == [(ChunkKind.FUNCTION, "fetch")]


def test_leftover_top_level_code_is_grouped_into_unbroken_runs():
    chunks = _chunk(LEFTOVER_SOURCE)

    assert [(chunk.kind, chunk.start_line, chunk.end_line) for chunk in chunks] == [
        (ChunkKind.MODULE, 1, 4),
        (ChunkKind.FUNCTION, 7, 8),
        (ChunkKind.MODULE, 11, 12),
    ]
    assert chunks[0].name == "<module>"


def test_an_empty_file_produces_no_chunks():
    assert _chunk("") == []
    assert _chunk("\n\n   \n") == []


def test_a_long_function_is_split_between_statements_into_contiguous_parts():
    source = _long_function_source(statement_count=200)
    parts = _chunk(source)

    assert len(parts) > 1
    _assert_contiguous_parts(parts, first_line=1, last_line=_line_count(source))
    assert all(len(part.text) <= MAX_CHUNK_CHARACTERS for part in parts)
    assert all(part.text.splitlines()[0].startswith("    value_") for part in parts[1:])


def test_only_later_parts_of_a_split_function_carry_the_signature():
    parts = _chunk(_long_function_source(statement_count=200))

    assert parts[0].signature is None
    assert parts[0].text.startswith("def long_function(value):")
    assert all(part.signature == "def long_function(value):" for part in parts[1:])
    assert all(part.name == "long_function" for part in parts)


def test_a_single_oversized_statement_is_split_between_lines():
    source_lines = ["def build_table():", "    return ["]
    source_lines += [f"        'entry_{index:04d}'," for index in range(400)]
    source_lines += ["    ]"]
    source = "\n".join(source_lines) + "\n"
    parts = _chunk(source)

    assert len(parts) > 1
    _assert_contiguous_parts(parts, first_line=1, last_line=len(source_lines))
    assert all(len(part.text) <= MAX_CHUNK_CHARACTERS for part in parts)


def test_a_long_class_outline_is_split_and_later_parts_carry_the_class_signature():
    source_lines = ["class Settings(Base):"]
    source_lines += [f"    setting_number_{index:04d} = {index}" for index in range(300)]
    source = "\n".join(source_lines) + "\n"
    parts = _chunk(source)

    assert len(parts) > 1
    _assert_contiguous_parts(parts, first_line=1, last_line=len(source_lines))
    assert parts[0].signature is None
    assert all(part.signature == "class Settings(Base):" for part in parts[1:])


def test_non_ascii_text_is_sliced_by_bytes_and_decoded_correctly():
    chunks = _chunk(NON_ASCII_SOURCE)

    assert chunks[0].text == 'def greet():\n    return "café ☕"'
    assert (chunks[1].start_line, chunks[1].text) == (5, "def after():\n    return 2")


def test_invalid_utf8_is_replaced_instead_of_raising():
    source = b'def greet():\n    return "caf\xe9"\n'

    chunks = chunk_python_source(SAMPLE_FILE_PATH, source)

    assert chunks[0].text == 'def greet():\n    return "caf�"'


def test_a_file_with_a_syntax_error_still_produces_chunks():
    chunks = _chunk(SYNTAX_ERROR_SOURCE)

    assert _chunk_named(chunks, "good").text == "def good():\n    return 1"
    _assert_every_non_blank_line_is_covered(SYNTAX_ERROR_SOURCE, chunks)


def test_every_chunk_except_class_outlines_matches_its_line_range_exactly():
    for source in _all_sample_sources():
        source_lines = source.split("\n")
        for chunk in _chunk(source):
            if chunk.kind == ChunkKind.CLASS:
                continue
            expected_text = "\n".join(source_lines[chunk.start_line - 1 : chunk.end_line])
            assert chunk.text == expected_text


def test_every_non_blank_line_falls_inside_some_chunk():
    for source in _all_sample_sources():
        _assert_every_non_blank_line_is_covered(source, _chunk(source))


def _chunk(source: str) -> list[CodeChunk]:
    return chunk_python_source(SAMPLE_FILE_PATH, source.encode("utf-8"))


def _chunk_named(chunks: list[CodeChunk], qualified_name: str) -> CodeChunk:
    matching_chunks = [chunk for chunk in chunks if chunk.qualified_name == qualified_name]
    assert len(matching_chunks) == 1
    return matching_chunks[0]


def _long_function_source(statement_count: int) -> str:
    statements = [
        f"    value_{index} = compute(value, {index})" for index in range(statement_count)
    ]
    source_lines = ["def long_function(value):", *statements, "    return value"]
    return "\n".join(source_lines) + "\n"


def _line_count(source: str) -> int:
    return len(source.splitlines())


def _assert_contiguous_parts(parts: list[CodeChunk], first_line: int, last_line: int) -> None:
    assert parts[0].start_line == first_line
    assert parts[-1].end_line == last_line
    for previous_part, next_part in zip(parts, parts[1:], strict=False):
        assert next_part.start_line == previous_part.end_line + 1
    assert [part.part_number for part in parts] == list(range(1, len(parts) + 1))
    assert all(part.part_count == len(parts) for part in parts)


def _assert_every_non_blank_line_is_covered(source: str, chunks: list[CodeChunk]) -> None:
    for line_number, line in enumerate(source.splitlines(), start=1):
        if not line.strip():
            continue
        covering_chunks = [
            chunk for chunk in chunks if chunk.start_line <= line_number <= chunk.end_line
        ]
        assert covering_chunks, f"line {line_number} is in no chunk: {line!r}"


def _all_sample_sources() -> list[str]:
    return [
        TOP_LEVEL_FUNCTION_SOURCE,
        CLASS_SOURCE,
        NESTED_CLASS_SOURCE,
        DECORATED_SOURCE,
        LEADING_COMMENT_SOURCE,
        NESTED_FUNCTION_SOURCE,
        ASYNC_SOURCE,
        LEFTOVER_SOURCE,
        NON_ASCII_SOURCE,
        SYNTAX_ERROR_SOURCE,
        _long_function_source(statement_count=200),
    ]
