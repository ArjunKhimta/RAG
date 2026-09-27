"""Confirm the Tree-sitter Python grammar loads and yields the node types the chunker needs.

Step 1 of the pipeline is one chunk per function or class, so `function_definition` and
`class_definition` are the two node types everything downstream depends on. This test fails loudly
if the grammar package is missing or its node names change under a version bump.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

FUNCTION_NODE_TYPE = "function_definition"

CLASS_NODE_TYPE = "class_definition"

SAMPLE_SOURCE = '''
def load_settings(path):
    return {"path": path}


class SettingsStore:
    pass
'''


@pytest.fixture
def parsed_sample() -> Tree:
    parser = Parser(Language(tree_sitter_python.language()))
    return parser.parse(SAMPLE_SOURCE.encode("utf-8"))


def test_the_sample_parses_without_errors(parsed_sample):
    assert parsed_sample.root_node.type == "module"
    assert not parsed_sample.root_node.has_error


def test_a_function_definition_is_found(parsed_sample):
    function_nodes = _find_nodes_of_type(parsed_sample.root_node, FUNCTION_NODE_TYPE)

    assert len(function_nodes) == 1
    assert _read_name(function_nodes[0]) == "load_settings"


def test_a_class_definition_is_found(parsed_sample):
    class_nodes = _find_nodes_of_type(parsed_sample.root_node, CLASS_NODE_TYPE)

    assert len(class_nodes) == 1
    assert _read_name(class_nodes[0]) == "SettingsStore"


def test_definitions_carry_the_line_range_a_citation_needs(parsed_sample):
    function_node = _find_nodes_of_type(parsed_sample.root_node, FUNCTION_NODE_TYPE)[0]

    first_line = function_node.start_point.row + 1
    last_line = function_node.end_point.row + 1

    assert (first_line, last_line) == (2, 3)


def _find_nodes_of_type(root: Node, node_type: str) -> list[Node]:
    return [node for node in _walk(root) if node.type == node_type]


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _read_name(definition_node: Node) -> str:
    name_node = definition_node.child_by_field_name("name")
    return name_node.text.decode("utf-8")
