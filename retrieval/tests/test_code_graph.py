from __future__ import annotations

from dataclasses import replace

import pytest

from retrieval.call_sites import find_file_calls
from retrieval.chunker import MAX_CHUNK_CHARACTERS, CodeChunk, chunk_python_source
from retrieval.code_graph import (
    CallGraph,
    CallGraphError,
    CallOutcome,
    build_call_graph,
    symbol_for,
)

APP_FILE = "src/pkg/app.py"

HELPERS_FILE = "src/pkg/helpers.py"

PACKAGE_INIT_FILE = "src/pkg/__init__.py"


def _build(files: dict[str, str], test_files: frozenset[str] = frozenset()) -> CallGraph:
    chunks: list[CodeChunk] = []
    file_calls = []
    for file_path, text in files.items():
        source = text.encode()
        is_test_file = file_path in test_files
        for chunk in chunk_python_source(file_path, source):
            chunks.append(replace(chunk, is_test_file=is_test_file))
        if not is_test_file:
            file_calls.append(find_file_calls(file_path, source))
    return build_call_graph(chunks, file_calls)


def _outcome_of(graph: CallGraph, called_name: str) -> tuple[CallOutcome, str | None]:
    matches = [
        (resolved.outcome, resolved.callee_symbol)
        for resolved in graph.resolved_calls
        if resolved.call_site.called_name == called_name
    ]
    assert len(matches) == 1, f"expected one call to {called_name}, found {len(matches)}"
    return matches[0]


def _callees_of(graph: CallGraph, qualified_name: str) -> tuple[str, ...]:
    for chunk, callees in graph.callees_by_chunk.items():
        if chunk.qualified_name == qualified_name:
            return callees
    return ()


def test_symbols_join_the_file_and_the_qualified_name():
    assert symbol_for("src/flask/app.py", "Flask.run") == "src/flask/app.py::Flask.run"


def test_a_self_call_to_a_method_of_the_same_class_resolves_to_it():
    graph = _build(
        {
            APP_FILE: (
                "class App:\n"
                "    def run(self):\n"
                "        return self.prepare()\n"
                "\n"
                "    def prepare(self):\n"
                "        return 1\n"
            )
        }
    )

    assert _outcome_of(graph, "prepare") == (
        CallOutcome.SAME_CLASS,
        symbol_for(APP_FILE, "App.prepare"),
    )
    assert _callees_of(graph, "App.run") == (symbol_for(APP_FILE, "App.prepare"),)


def test_a_name_defined_in_the_same_file_resolves_including_classes_and_their_methods():
    graph = _build(
        {
            APP_FILE: (
                "class Config:\n"
                "    def load(self):\n"
                "        return 1\n"
                "\n"
                "def helper():\n"
                "    return 1\n"
                "\n"
                "def run():\n"
                "    helper()\n"
                "    Config.load(None)\n"
                "    return Config()\n"
            )
        }
    )

    assert _callees_of(graph, "run") == (
        symbol_for(APP_FILE, "Config"),
        symbol_for(APP_FILE, "Config.load"),
        symbol_for(APP_FILE, "helper"),
    )
    assert _outcome_of(graph, "helper")[0] == CallOutcome.SAME_FILE
    assert _outcome_of(graph, "load")[0] == CallOutcome.SAME_FILE


def test_imported_names_and_modules_resolve_through_relative_imports():
    graph = _build(
        {
            HELPERS_FILE: "def format_name(value):\n    return value\n\ndef slug(value):\n"
            "    return value\n",
            APP_FILE: (
                "from .helpers import format_name\n"
                "from . import helpers\n"
                "\n"
                "def run(value):\n"
                "    format_name(value)\n"
                "    return helpers.slug(value)\n"
            ),
        }
    )

    assert _outcome_of(graph, "format_name") == (
        CallOutcome.IMPORTED,
        symbol_for(HELPERS_FILE, "format_name"),
    )
    assert _outcome_of(graph, "slug") == (CallOutcome.IMPORTED, symbol_for(HELPERS_FILE, "slug"))


def test_an_import_from_a_package_follows_its_re_export_to_the_definition():
    graph = _build(
        {
            PACKAGE_INIT_FILE: "from .app import Flask as Flask\n",
            APP_FILE: "class Flask:\n    pass\n",
            "examples/demo.py": "from pkg import Flask\n\ndef create():\n    return Flask()\n",
        }
    )

    assert _outcome_of(graph, "Flask") == (CallOutcome.IMPORTED, symbol_for(APP_FILE, "Flask"))


def test_names_imported_from_outside_the_repository_never_fall_back_to_a_same_name_definition():
    graph = _build(
        {
            HELPERS_FILE: "def redirect(location):\n    return location\n",
            APP_FILE: (
                "import os\n"
                "from werkzeug import redirect\n"
                "\n"
                "def run(path):\n"
                "    os.path.join(path)\n"
                "    return redirect(path)\n"
            ),
        }
    )

    assert _outcome_of(graph, "join") == (CallOutcome.EXTERNAL, None)
    assert _outcome_of(graph, "redirect") == (CallOutcome.EXTERNAL, None)


def test_built_ins_and_local_names_stay_unresolved_unless_the_file_has_a_star_import():
    helpers_source = "def callback():\n    return 1\n"
    caller_source = "def run(callback):\n    len([])\n    return callback()\n"

    graph = _build({HELPERS_FILE: helpers_source, APP_FILE: caller_source})
    star_graph = _build(
        {HELPERS_FILE: helpers_source, APP_FILE: "from .helpers import *\n\n" + caller_source}
    )

    assert _outcome_of(graph, "len") == (CallOutcome.BUILTIN, None)
    assert _outcome_of(graph, "callback") == (CallOutcome.LOCAL_OR_UNKNOWN, None)
    assert _outcome_of(star_graph, "callback") == (
        CallOutcome.UNIQUE_NAME,
        symbol_for(HELPERS_FILE, "callback"),
    )


@pytest.mark.parametrize(
    ("definitions", "expected_outcome"),
    [
        ("class Page:\n    def render(self):\n        return 1\n", CallOutcome.UNIQUE_NAME),
        (
            "class Page:\n    def render(self):\n        return 1\n\n"
            "class Card:\n    def render(self):\n        return 2\n",
            CallOutcome.AMBIGUOUS,
        ),
        ("class Page:\n    pass\n", CallOutcome.NOT_DEFINED),
    ],
)
def test_other_attribute_calls_resolve_only_when_exactly_one_definition_has_the_name(
    definitions, expected_outcome
):
    graph = _build(
        {HELPERS_FILE: definitions, APP_FILE: "def show(page):\n    return page.render()\n"}
    )

    assert _outcome_of(graph, "render")[0] == expected_outcome


def test_built_in_container_method_names_are_never_resolved_by_name_alone():
    graph = _build(
        {
            HELPERS_FILE: "class Registry:\n    def get(self, key):\n        return key\n",
            APP_FILE: "def lookup(config):\n    return config.get('name')\n",
        }
    )

    assert _outcome_of(graph, "get") == (CallOutcome.COMMON_NAME, None)


def test_an_inherited_self_call_and_a_super_call_resolve_by_unique_method_name():
    graph = _build(
        {
            HELPERS_FILE: (
                "class Base:\n"
                "    def save(self):\n"
                "        return 1\n"
                "\n"
                "    def validate(self):\n"
                "        return 1\n"
            ),
            APP_FILE: (
                "from .helpers import Base\n"
                "\n"
                "class Child(Base):\n"
                "    def save(self):\n"
                "        self.validate()\n"
                "        return super().save()\n"
            ),
        }
    )

    assert _outcome_of(graph, "validate") == (
        CallOutcome.UNIQUE_NAME,
        symbol_for(HELPERS_FILE, "Base.validate"),
    )
    super_calls = [
        resolved
        for resolved in graph.resolved_calls
        if resolved.call_site.called_name == "save"
    ]
    assert [(resolved.outcome, resolved.callee_symbol) for resolved in super_calls] == [
        (CallOutcome.UNIQUE_NAME, symbol_for(HELPERS_FILE, "Base.save"))
    ]


def test_a_recursive_call_is_resolved_but_adds_no_edge():
    graph = _build({APP_FILE: "def walk(node):\n    return walk(node.child)\n"})

    assert _outcome_of(graph, "walk") == (CallOutcome.SAME_FILE, symbol_for(APP_FILE, "walk"))
    assert graph.callees_by_chunk == {}


def test_each_part_of_a_split_definition_lists_only_its_own_calls():
    filler_lines = [f"    value_{index} = {index} * {'1' * 60}\n" for index in range(80)]
    source = (
        "def first():\n    return 1\n\n"
        "def last():\n    return 2\n\n"
        "def long_function():\n"
        "    first()\n" + "".join(filler_lines) + "    return last()\n"
    )
    assert len(source) > MAX_CHUNK_CHARACTERS

    graph = _build({APP_FILE: source})

    parts = sorted(
        (chunk for chunk in graph.callees_by_chunk if chunk.qualified_name == "long_function"),
        key=lambda chunk: chunk.part_number,
    )
    assert len(parts) == 2
    assert graph.callees_by_chunk[parts[0]] == (symbol_for(APP_FILE, "first"),)
    assert graph.callees_by_chunk[parts[1]] == (symbol_for(APP_FILE, "last"),)


def test_test_files_are_neither_callers_nor_targets():
    graph = _build(
        {
            "tests/test_page.py": "class FakePage:\n    def render(self):\n        return 1\n",
            APP_FILE: "def show(page):\n    return page.render()\n",
        },
        test_files=frozenset({"tests/test_page.py"}),
    )

    assert _outcome_of(graph, "render") == (CallOutcome.NOT_DEFINED, None)


def test_a_call_site_without_a_matching_chunk_is_an_error():
    file_calls = find_file_calls(APP_FILE, b"def run():\n    return helper()\n")

    with pytest.raises(CallGraphError, match="run"):
        build_call_graph([], [file_calls])
