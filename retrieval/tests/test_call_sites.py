from __future__ import annotations

import pytest

from retrieval.call_sites import (
    CallShape,
    CallSite,
    ImportBinding,
    find_file_calls,
    module_name_for,
    package_name_for,
)

SAMPLE_FILE_PATH = "src/pkg/sample.py"

CALL_SHAPES_SOURCE = """\
def run(self, handlers):
    load()
    self.save()
    cls.build()
    super().close()
    os.path.join("a", "b")
    self.app.handle(1)
    "".join(handlers)
    handlers["key"]()
"""

CLASS_SOURCE = """\
class Store:
    limit = compute_limit()

    def size(self):
        return count(self)

    class Inner:
        def ping(self):
            return pong()
"""

NESTED_AND_DECORATED_SOURCE = """\
@register("name")
def outer():
    def inner():
        return helper()

    return apply(lambda value: convert(value))
"""

MODULE_LEVEL_SOURCE = """\
app = create_app()

if CONDITION:
    def hidden():
        return secret()
"""

IMPORTS_SOURCE = """\
import os.path
import json as json_module
from collections import OrderedDict as Ordered, deque
from . import helpers
from .globals import request
from ..shared.tools import tool as shared_tool


def lazy():
    from .late import late_value
    return late_value()
"""


def test_each_call_shape_is_recorded_with_its_name_and_receiver():
    file_calls = find_file_calls(SAMPLE_FILE_PATH, CALL_SHAPES_SOURCE.encode())

    shapes = [
        (call_site.shape, call_site.called_name, call_site.receiver_parts)
        for call_site in file_calls.call_sites
    ]
    assert shapes == [
        (CallShape.NAME, "load", ()),
        (CallShape.SELF, "save", ()),
        (CallShape.SELF, "build", ()),
        (CallShape.SUPER, "close", ()),
        (CallShape.NAME, "super", ()),
        (CallShape.ATTRIBUTE, "join", ("os", "path")),
        (CallShape.ATTRIBUTE, "handle", ("self", "app")),
        (CallShape.ATTRIBUTE, "join", ()),
        (CallShape.UNSUPPORTED, None, ()),
    ]


def test_call_sites_carry_the_caller_and_the_line_of_the_call():
    file_calls = find_file_calls(SAMPLE_FILE_PATH, CALL_SHAPES_SOURCE.encode())

    assert file_calls.call_sites[0] == CallSite(
        caller_qualified_name="run",
        caller_parent_class=None,
        line=2,
        shape=CallShape.NAME,
        called_name="load",
    )
    assert file_calls.call_sites[-1].line == 9


def test_method_calls_name_their_class_and_class_level_calls_are_left_out():
    file_calls = find_file_calls(SAMPLE_FILE_PATH, CLASS_SOURCE.encode())

    callers = [
        (call_site.caller_qualified_name, call_site.caller_parent_class, call_site.called_name)
        for call_site in file_calls.call_sites
    ]
    assert callers == [
        ("Store.size", "Store", "count"),
        ("Store.Inner.ping", "Store.Inner", "pong"),
    ]


def test_calls_in_nested_functions_lambdas_and_decorators_belong_to_the_enclosing_chunk():
    file_calls = find_file_calls(SAMPLE_FILE_PATH, NESTED_AND_DECORATED_SOURCE.encode())

    called = [
        (call_site.caller_qualified_name, call_site.called_name)
        for call_site in file_calls.call_sites
    ]
    assert called == [
        ("outer", "register"),
        ("outer", "helper"),
        ("outer", "apply"),
        ("outer", "convert"),
    ]


def test_module_level_code_and_definitions_inside_it_have_no_call_sites():
    file_calls = find_file_calls(SAMPLE_FILE_PATH, MODULE_LEVEL_SOURCE.encode())

    assert file_calls.call_sites == ()


@pytest.mark.parametrize(
    ("file_path", "module", "package"),
    [
        ("src/flask/json/__init__.py", "flask.json", "flask.json"),
        ("src/flask/app.py", "flask.app", "flask"),
        ("examples/demo/db.py", "examples.demo.db", "examples.demo"),
        ("setup.py", "setup", ""),
        ("src.py", "src", ""),
    ],
)
def test_module_and_package_names_come_from_the_path(file_path, module, package):
    assert module_name_for(file_path) == module
    assert package_name_for(file_path) == package


def test_imports_anywhere_in_the_file_are_bound_to_absolute_module_names():
    file_calls = find_file_calls("src/pkg/sub/module.py", IMPORTS_SOURCE.encode())

    assert file_calls.module == "pkg.sub.module"
    assert dict(file_calls.imports) == {
        "os": ImportBinding("os", None),
        "json_module": ImportBinding("json", None),
        "Ordered": ImportBinding("collections", "OrderedDict"),
        "deque": ImportBinding("collections", "deque"),
        "helpers": ImportBinding("pkg.sub", "helpers"),
        "request": ImportBinding("pkg.sub.globals", "request"),
        "shared_tool": ImportBinding("pkg.shared.tools", "tool"),
        "late_value": ImportBinding("pkg.sub.late", "late_value"),
    }
    assert not file_calls.has_star_import


def test_relative_imports_in_a_package_init_start_from_the_package_itself():
    source = b"from .app import Flask\nfrom . import json\n"

    file_calls = find_file_calls("src/flask/__init__.py", source)

    assert dict(file_calls.imports) == {
        "Flask": ImportBinding("flask.app", "Flask"),
        "json": ImportBinding("flask", "json"),
    }


def test_a_star_import_is_flagged_and_a_relative_import_above_the_top_is_ignored():
    source = b"from .tools import *\nfrom .... import too_far\n"

    file_calls = find_file_calls("src/pkg/module.py", source)

    assert file_calls.has_star_import
    assert dict(file_calls.imports) == {}
