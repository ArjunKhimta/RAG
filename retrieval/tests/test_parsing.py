"""Confirm the shared Tree-sitter setup parses Python and converts rows to 1-indexed lines.

Step 1 of the pipeline is one chunk per function or class, so `function_definition` and
`class_definition` are the two node types everything downstream depends on. These tests fail
loudly if the grammar package is missing or its node names change under a version bump.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from tree_sitter import Node, Tree

from retrieval.parsing import LineRange, line_range_of, parse_python_source

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
    return parse_python_source(SAMPLE_SOURCE.encode("utf-8"))


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


def test_a_multi_line_definition_gets_1_indexed_lines(parsed_sample):
    function_node = _find_nodes_of_type(parsed_sample.root_node, FUNCTION_NODE_TYPE)[0]

    assert line_range_of(function_node) == LineRange(first_line=2, last_line=3)


def test_a_single_line_node_starts_and_ends_on_the_same_line(parsed_sample):
    class_node = _find_nodes_of_type(parsed_sample.root_node, CLASS_NODE_TYPE)[0]
    class_name_node = class_node.child_by_field_name("name")

    assert line_range_of(class_name_node) == LineRange(first_line=6, last_line=6)


def test_a_node_ending_at_column_zero_does_not_claim_the_next_line(parsed_sample):
    module_node = parsed_sample.root_node

    assert module_node.end_point.column == 0
    assert line_range_of(module_node).last_line == 7


def test_line_ranges_can_be_read_for_every_node_of_a_large_tree():
    """tree-sitter 0.26.0 segfaults here after about 1,300 nodes; 0.25.2 does not."""
    entry_lines = [f"    'entry_{index:04d}'," for index in range(400)]
    source = "\n".join(["TABLE = [", *entry_lines, "]"]) + "\n"
    tree = parse_python_source(source.encode("utf-8"))

    line_ranges = [line_range_of(node) for node in _walk(tree.root_node)]

    assert len(line_ranges) > 1300
    assert line_ranges[0] == LineRange(first_line=1, last_line=402)


def _find_nodes_of_type(root: Node, node_type: str) -> list[Node]:
    return [node for node in _walk(root) if node.type == node_type]


def _walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _read_name(definition_node: Node) -> str:
    name_node = definition_node.child_by_field_name("name")
    return name_node.text.decode("utf-8")
