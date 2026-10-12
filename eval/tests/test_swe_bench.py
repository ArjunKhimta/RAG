from __future__ import annotations

import pytest
from retrieval.chunker import MAX_CHUNK_CHARACTERS

from evaluation.swe_bench import (
    CHANGED_LINE_REASON,
    INSERTION_REASON,
    FileChange,
    PatchFormatError,
    expected_changes,
    read_file_changes,
    split_module_level,
)

SOURCE = b"""import os
import sys


class Client:
    retries = 3

    def send(self, request):
        prepared = self.prepare(request)
        return prepared

    def prepare(self, request):
        return request


def helper(value):
    return value
"""


def patch_of(*hunks: str, path: str = "pkg/client.py") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n" + "".join(hunks)


def change(removed: tuple[int, ...] = (), inserted: tuple[int, ...] = ()) -> FileChange:
    return FileChange("pkg/client.py", removed, inserted)


def names_and_reasons(changes):
    return [(item.qualified_name, item.reasons) for item in changes]


class TestReadingPatches:
    def test_a_rewritten_line_counts_as_removed_and_not_as_an_insertion(self):
        patch = patch_of(
            "@@ -8,3 +8,3 @@ class Client:\n",
            "     def send(self, request):\n",
            "-        prepared = self.prepare(request)\n",
            "+        prepared = self.prepare(request, strict=True)\n",
            "         return prepared\n",
        )

        assert read_file_changes(patch) == [FileChange("pkg/client.py", (9,), ())]

    def test_added_lines_alone_record_the_old_line_they_follow(self):
        patch = patch_of(
            "@@ -8,2 +8,3 @@ class Client:\n",
            "     def send(self, request):\n",
            "+        self.check(request)\n",
            "         prepared = self.prepare(request)\n",
        )

        assert read_file_changes(patch) == [FileChange("pkg/client.py", (), (8,))]

    def test_line_numbers_follow_earlier_hunks_and_context(self):
        patch = patch_of(
            "@@ -1,3 +1,4 @@\n",
            " import os\n",
            "+import re\n",
            " import sys\n",
            " \n",
            "@@ -16,2 +17,2 @@ def helper(value):\n",
            " def helper(value):\n",
            "-    return value\n",
            "+    return value or None\n",
        )

        assert read_file_changes(patch) == [FileChange("pkg/client.py", (17,), (1,))]

    def test_several_files_are_read_in_order(self):
        first = patch_of("@@ -1,1 +1,1 @@\n", "-a\n", "+b\n", path="pkg/one.py")
        second = patch_of("@@ -2,1 +2,1 @@\n", "-c\n", "+d\n", path="pkg/two.py")

        changes = read_file_changes(first + second)

        assert [(item.file_path, item.removed_lines) for item in changes] == [
            ("pkg/one.py", (1,)),
            ("pkg/two.py", (2,)),
        ]

    def test_a_new_file_has_nothing_at_the_base_commit(self):
        patch = "--- /dev/null\n+++ b/pkg/new.py\n@@ -0,0 +1,2 @@\n+def new():\n+    return 1\n"

        assert read_file_changes(patch) == [FileChange("pkg/new.py", (), (0,), is_new_file=True)]

    def test_the_missing_newline_marker_is_not_a_line(self):
        patch = patch_of(
            "@@ -17,1 +17,1 @@\n",
            "-    return value\n",
            "\\ No newline at end of file\n",
            "+    return value or None\n",
            "\\ No newline at end of file\n",
        )

        assert read_file_changes(patch) == [FileChange("pkg/client.py", (17,), ())]

    @pytest.mark.parametrize(
        "patch",
        [
            "not a diff at all\n",
            "--- a/pkg/client.py\n@@ -1 +1 @@\n",
            patch_of("@@ nonsense @@\n"),
            patch_of("@@ -1,1 +1,1 @@\n", "?odd line\n"),
            "--- pkg/client.py\n+++ pkg/client.py\n",
        ],
    )
    def test_anything_else_is_refused(self, patch):
        with pytest.raises(PatchFormatError):
            read_file_changes(patch)


class TestExpectedDefinitions:
    def test_a_line_inside_a_method_counts_for_the_method_not_its_class(self):
        changes = expected_changes(change(removed=(9,)), SOURCE)

        assert names_and_reasons(changes) == [("Client.send", (CHANGED_LINE_REASON,))]
        assert (changes[0].start_line, changes[0].end_line) == (8, 10)
        assert changes[0].kind == "method"

    def test_a_line_in_the_class_body_outside_any_method_counts_for_the_class(self):
        changes = expected_changes(change(removed=(6,)), SOURCE)

        assert names_and_reasons(changes) == [("Client", (CHANGED_LINE_REASON,))]

    def test_an_insertion_inside_a_definition_counts_for_that_definition(self):
        changes = expected_changes(change(inserted=(8,)), SOURCE)

        assert names_and_reasons(changes) == [("Client.send", (INSERTION_REASON,))]

    def test_an_insertion_after_a_blank_line_between_functions_counts_for_the_next_one(self):
        changes = expected_changes(change(inserted=(15,)), SOURCE)

        assert names_and_reasons(changes) == [("helper", (INSERTION_REASON,))]
        assert changes[0].lines == (16,)

    def test_module_level_lines_count_for_the_module_chunk(self):
        changes = expected_changes(change(removed=(2,)), SOURCE)

        assert [(item.kind, item.qualified_name) for item in changes] == [("module", "<module>")]

    def test_several_lines_in_one_definition_make_one_expected_definition(self):
        changes = expected_changes(change(removed=(9, 10), inserted=(8,)), SOURCE)

        assert len(changes) == 1
        assert changes[0].reasons == (CHANGED_LINE_REASON, INSERTION_REASON)
        assert changes[0].lines == (8, 9, 10)

    def test_definitions_come_back_in_file_order(self):
        changes = expected_changes(change(removed=(17, 13, 9)), SOURCE)

        assert [item.qualified_name for item in changes] == [
            "Client.send",
            "Client.prepare",
            "helper",
        ]

    def test_a_split_definition_is_expected_whole_from_any_of_its_parts(self):
        statement_count = MAX_CHUNK_CHARACTERS // 20
        body = "".join(f"    value_{index} = {index}\n" for index in range(statement_count))
        source = ("def long_function():\n" + body + "    return value_0\n").encode()
        last_line = statement_count + 2

        changes = expected_changes(FileChange("pkg/long.py", (last_line,), ()), source)

        assert [(item.qualified_name, item.start_line, item.end_line) for item in changes] == [
            ("long_function", 1, last_line)
        ]

    def test_a_new_file_expects_nothing(self):
        assert expected_changes(FileChange("pkg/new.py", (), (0,), is_new_file=True), b"") == []


class TestModuleLevel:
    def test_module_level_changes_are_set_apart_when_a_definition_changed_too(self):
        changes = expected_changes(change(removed=(2, 9)), SOURCE)

        definitions, module_level = split_module_level(changes)

        assert [item.qualified_name for item in definitions] == ["Client.send"]
        assert [item.qualified_name for item in module_level] == ["<module>"]

    def test_module_level_changes_stay_expected_when_they_are_all_there_is(self):
        changes = expected_changes(change(removed=(2,)), SOURCE)

        definitions, module_level = split_module_level(changes)

        assert [item.qualified_name for item in definitions] == ["<module>"]
        assert module_level == []
