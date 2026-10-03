"""Find the calls inside each function and method of one Python file, and the names it imports.

Only calls inside function and method chunks are collected. Calls at module or class level, such
as `app = Flask(__name__)`, have no function chunk to belong to. The walk follows the chunker's
own rules for which definitions become chunks, so every call site names a chunk that exists.
Calls in nested functions and lambdas belong to the enclosing chunk, as the chunker keeps nested
functions inside their parent. Calls in a function's decorators belong to it too, because the
decorators are part of its chunk.

Each call is recorded by its shape and never resolved here:
- `foo()` is a name call
- `self.foo()` and `cls.foo()` are self calls
- `super().foo()` is a super call
- `a.b.foo()` is an attribute call; when the receiver is a plain dotted chain of names, its parts
  are kept so the resolver can follow imported modules
- anything else, such as `handlers[key]()` or `factory()()`, is unsupported and has no name

Imports are collected from the whole file, including those inside functions and
`if TYPE_CHECKING:` blocks, and turned into absolute module names. A file's module name is its
path without `.py`, without a final `__init__`, and without a leading `src/` folder, so
`src/flask/json/__init__.py` is the module `flask.json`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from tree_sitter import Node

from retrieval.chunker import (
    CLASS_NODE_TYPE,
    SOURCE_ENCODING,
    UNNAMED_DEFINITION,
    body_items,
    definition_inside,
    qualify,
)
from retrieval.parsing import line_range_of, parse_python_source

CALL_NODE_TYPE = "call"

IDENTIFIER_NODE_TYPE = "identifier"

ATTRIBUTE_NODE_TYPE = "attribute"

DOTTED_NAME_NODE_TYPE = "dotted_name"

ALIASED_IMPORT_NODE_TYPE = "aliased_import"

RELATIVE_IMPORT_NODE_TYPE = "relative_import"

IMPORT_PREFIX_NODE_TYPE = "import_prefix"

WILDCARD_IMPORT_NODE_TYPE = "wildcard_import"

IMPORT_NODE_TYPE = "import_statement"

FROM_IMPORT_NODE_TYPE = "import_from_statement"

SELF_RECEIVER_NAMES = frozenset({"self", "cls"})

SUPER_FUNCTION_NAME = "super"

SOURCE_FOLDER_NAME = "src"

PACKAGE_INIT_NAME = "__init__"

MODULE_SEPARATOR = "."


class CallShape(StrEnum):
    NAME = "name"
    SELF = "self"
    SUPER = "super"
    ATTRIBUTE = "attribute"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CallSite:
    """One call inside a function or method chunk.

    `line` is the 1-indexed line where the call starts. `called_name` is None only for an
    unsupported shape. `receiver_parts` holds the dotted names before the called name, such as
    `("os", "path")` for `os.path.join()`, and is empty when the receiver is anything else.
    """

    caller_qualified_name: str
    caller_parent_class: str | None
    line: int
    shape: CallShape
    called_name: str | None
    receiver_parts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportBinding:
    """What an imported name refers to: a module, or a name imported from a module.

    `imported_name` is None when the bound name is the module itself, as with `import os`.
    """

    module: str
    imported_name: str | None


@dataclass(frozen=True)
class FileCalls:
    file_path: str
    module: str
    call_sites: tuple[CallSite, ...]
    imports: Mapping[str, ImportBinding]
    has_star_import: bool


def find_file_calls(file_path: str, source: bytes) -> FileCalls:
    """Return the call sites and imports of one Python file. `file_path` is repo-relative."""
    tree = parse_python_source(source)
    module = module_name_for(file_path)
    package = package_name_for(file_path)
    import_collector = _ImportCollector(package)
    import_collector.collect(tree.root_node)
    return FileCalls(
        file_path=file_path,
        module=module,
        call_sites=tuple(_module_call_sites(tree.root_node)),
        imports=import_collector.bindings,
        has_star_import=import_collector.has_star_import,
    )


def module_name_for(file_path: str) -> str:
    return MODULE_SEPARATOR.join(_module_parts_of(file_path))


def package_name_for(file_path: str) -> str:
    """Return the package that relative imports in the file start from.

    A package's `__init__.py` imports relative to the package itself, while any other module
    imports relative to the package that contains it.
    """
    module_parts = _module_parts_of(file_path)
    if PurePosixPath(file_path).stem == PACKAGE_INIT_NAME:
        return MODULE_SEPARATOR.join(module_parts)
    return MODULE_SEPARATOR.join(module_parts[:-1])


def _module_parts_of(file_path: str) -> list[str]:
    path = PurePosixPath(file_path)
    parts = [*path.parent.parts, path.stem]
    if parts[-1] == PACKAGE_INIT_NAME:
        parts = parts[:-1]
    if len(parts) > 1 and parts[0] == SOURCE_FOLDER_NAME:
        parts = parts[1:]
    return parts


def _module_call_sites(module_node: Node) -> Iterator[CallSite]:
    for item in module_node.named_children:
        definition_node = definition_inside(item)
        if definition_node is not None:
            yield from _definition_call_sites(item, definition_node, parent_class=None)


def _definition_call_sites(
    item: Node, definition_node: Node, parent_class: str | None
) -> Iterator[CallSite]:
    """Yield the calls of a chunked definition, where `item` includes any decorators."""
    qualified_name = qualify(_definition_name(definition_node), parent_class)
    if definition_node.type == CLASS_NODE_TYPE:
        for member_item in body_items(definition_node):
            member_definition = definition_inside(member_item)
            if member_definition is not None:
                yield from _definition_call_sites(
                    member_item, member_definition, parent_class=qualified_name
                )
        return
    for call_node in _call_nodes_within(item):
        yield _call_site_of(call_node, qualified_name, parent_class)


def _call_nodes_within(node: Node) -> Iterator[Node]:
    """Yield every call node inside `node`, in source order."""
    pending_nodes = [node]
    while pending_nodes:
        current_node = pending_nodes.pop()
        if current_node.type == CALL_NODE_TYPE:
            yield current_node
        pending_nodes.extend(reversed(current_node.named_children))


def _call_site_of(
    call_node: Node, caller_qualified_name: str, caller_parent_class: str | None
) -> CallSite:
    line = line_range_of(call_node).first_line
    function_node = call_node.child_by_field_name("function")
    shape, called_name, receiver_parts = _describe_called_function(function_node)
    return CallSite(
        caller_qualified_name=caller_qualified_name,
        caller_parent_class=caller_parent_class,
        line=line,
        shape=shape,
        called_name=called_name,
        receiver_parts=receiver_parts,
    )


def _describe_called_function(
    function_node: Node | None,
) -> tuple[CallShape, str | None, tuple[str, ...]]:
    if function_node is None:
        return CallShape.UNSUPPORTED, None, ()
    if function_node.type == IDENTIFIER_NODE_TYPE:
        return CallShape.NAME, _text_of(function_node), ()
    if function_node.type != ATTRIBUTE_NODE_TYPE:
        return CallShape.UNSUPPORTED, None, ()
    attribute_node = function_node.child_by_field_name("attribute")
    receiver_node = function_node.child_by_field_name("object")
    if attribute_node is None or receiver_node is None:
        return CallShape.UNSUPPORTED, None, ()
    called_name = _text_of(attribute_node)
    if _is_self_receiver(receiver_node):
        return CallShape.SELF, called_name, ()
    if _is_super_call(receiver_node):
        return CallShape.SUPER, called_name, ()
    return CallShape.ATTRIBUTE, called_name, _dotted_parts_of(receiver_node)


def _is_self_receiver(receiver_node: Node) -> bool:
    is_identifier = receiver_node.type == IDENTIFIER_NODE_TYPE
    return is_identifier and _text_of(receiver_node) in SELF_RECEIVER_NAMES


def _is_super_call(receiver_node: Node) -> bool:
    if receiver_node.type != CALL_NODE_TYPE:
        return False
    function_node = receiver_node.child_by_field_name("function")
    if function_node is None or function_node.type != IDENTIFIER_NODE_TYPE:
        return False
    return _text_of(function_node) == SUPER_FUNCTION_NAME


def _dotted_parts_of(node: Node) -> tuple[str, ...]:
    """Return `("a", "b")` for the expression `a.b`, or `()` if it is not a chain of names."""
    if node.type == IDENTIFIER_NODE_TYPE:
        return (_text_of(node),)
    if node.type != ATTRIBUTE_NODE_TYPE:
        return ()
    receiver_node = node.child_by_field_name("object")
    attribute_node = node.child_by_field_name("attribute")
    if receiver_node is None or attribute_node is None:
        return ()
    receiver_parts = _dotted_parts_of(receiver_node)
    if not receiver_parts:
        return ()
    return (*receiver_parts, _text_of(attribute_node))


class _ImportCollector:
    def __init__(self, package: str) -> None:
        self._package_parts = package.split(MODULE_SEPARATOR) if package else []
        self.bindings: dict[str, ImportBinding] = {}
        self.has_star_import = False

    def collect(self, root_node: Node) -> None:
        pending_nodes = [root_node]
        while pending_nodes:
            current_node = pending_nodes.pop()
            if current_node.type == IMPORT_NODE_TYPE:
                self._collect_import(current_node)
            elif current_node.type == FROM_IMPORT_NODE_TYPE:
                self._collect_from_import(current_node)
            else:
                pending_nodes.extend(reversed(current_node.named_children))

    def _collect_import(self, import_node: Node) -> None:
        """Bind `import a.b` as `a`, and `import a.b as c` as `c`, each to a module."""
        for name_node in import_node.children_by_field_name("name"):
            if name_node.type == ALIASED_IMPORT_NODE_TYPE:
                module = _text_of_field(name_node, "name")
                alias = _text_of_field(name_node, "alias")
                if module and alias:
                    self.bindings[alias] = ImportBinding(module, None)
                continue
            module = _text_of(name_node)
            root_name = module.split(MODULE_SEPARATOR)[0]
            self.bindings[root_name] = ImportBinding(root_name, None)

    def _collect_from_import(self, import_node: Node) -> None:
        module_node = import_node.child_by_field_name("module_name")
        if module_node is None:
            return
        module = self._absolute_module_of(module_node)
        if module is None:
            return
        for child in import_node.named_children:
            if child.type == WILDCARD_IMPORT_NODE_TYPE:
                self.has_star_import = True
        for name_node in import_node.children_by_field_name("name"):
            if name_node.type == ALIASED_IMPORT_NODE_TYPE:
                imported_name = _text_of_field(name_node, "name")
                bound_name = _text_of_field(name_node, "alias")
            else:
                imported_name = _text_of(name_node)
                bound_name = imported_name
            if imported_name and bound_name:
                self.bindings[bound_name] = ImportBinding(module, imported_name)

    def _absolute_module_of(self, module_node: Node) -> str | None:
        """Return the absolute module name, or None for a relative import above the top package."""
        if module_node.type != RELATIVE_IMPORT_NODE_TYPE:
            return _text_of(module_node)
        level = 0
        named_parts: list[str] = []
        for child in module_node.named_children:
            if child.type == IMPORT_PREFIX_NODE_TYPE:
                level = len(_text_of(child))
            elif child.type == DOTTED_NAME_NODE_TYPE:
                named_parts = _text_of(child).split(MODULE_SEPARATOR)
        levels_up = level - 1
        if levels_up > len(self._package_parts):
            return None
        base_parts = self._package_parts[: len(self._package_parts) - levels_up]
        module_parts = [*base_parts, *named_parts]
        if not module_parts:
            return None
        return MODULE_SEPARATOR.join(module_parts)


def _definition_name(definition_node: Node) -> str:
    name_node = definition_node.child_by_field_name("name")
    if name_node is None:
        return UNNAMED_DEFINITION
    return _text_of(name_node)


def _text_of_field(node: Node, field_name: str) -> str | None:
    field_node = node.child_by_field_name(field_name)
    if field_node is None:
        return None
    return _text_of(field_node)


def _text_of(node: Node) -> str:
    raw_text = node.text or b""
    return raw_text.decode(SOURCE_ENCODING, errors="replace")
