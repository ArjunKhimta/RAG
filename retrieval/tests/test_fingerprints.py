from __future__ import annotations

import hashlib
import json

from retrieval.fingerprints import (
    ROOT_FOLDER_PATH,
    PathFingerprint,
    build_version_fingerprints,
    fingerprint_bytes,
    parent_folder_of,
)

APP_FINGERPRINT = fingerprint_bytes(b"def app():\n    return 1\n")

HELPERS_FINGERPRINT = fingerprint_bytes(b"def helper():\n    return 2\n")

SETUP_FINGERPRINT = fingerprint_bytes(b"from setuptools import setup\n")

FILES = [
    PathFingerprint("src/package/app.py", APP_FINGERPRINT),
    PathFingerprint("src/package/helpers.py", HELPERS_FINGERPRINT),
    PathFingerprint("setup.py", SETUP_FINGERPRINT),
]


def _sha256_of_entries(entries: list[list[str]]) -> str:
    return hashlib.sha256(json.dumps(entries).encode("ascii")).hexdigest()


def _folder(fingerprints, path: str) -> str:
    return next(folder.fingerprint for folder in fingerprints.folders if folder.path == path)


def _with_file(path: str, fingerprint: str) -> list[PathFingerprint]:
    return [file for file in FILES if file.path != path] + [PathFingerprint(path, fingerprint)]


def test_a_file_fingerprint_is_the_sha256_of_its_bytes():
    content = b"print('hello')\n"

    assert fingerprint_bytes(content) == hashlib.sha256(content).hexdigest()


def test_each_folder_hashes_its_entries_in_name_order_and_the_root_covers_everything():
    fingerprints = build_version_fingerprints(FILES)

    package = _sha256_of_entries(
        [["file", "app.py", APP_FINGERPRINT], ["file", "helpers.py", HELPERS_FINGERPRINT]]
    )
    source = _sha256_of_entries([["folder", "package", package]])
    root = _sha256_of_entries([["file", "setup.py", SETUP_FINGERPRINT], ["folder", "src", source]])
    assert _folder(fingerprints, "src/package") == package
    assert _folder(fingerprints, "src") == source
    assert _folder(fingerprints, ROOT_FOLDER_PATH) == root
    assert fingerprints.root == root


def test_folders_and_files_are_listed_in_path_order():
    fingerprints = build_version_fingerprints(FILES)

    assert [folder.path for folder in fingerprints.folders] == ["", "src", "src/package"]
    assert [file.path for file in fingerprints.files] == [
        "setup.py",
        "src/package/app.py",
        "src/package/helpers.py",
    ]


def test_the_order_files_arrive_in_does_not_change_any_fingerprint():
    assert build_version_fingerprints(FILES) == build_version_fingerprints(reversed(FILES))


def test_a_changed_file_changes_its_folders_up_to_the_root_and_nothing_beside_them():
    before = build_version_fingerprints(FILES)
    changed = fingerprint_bytes(b"def helper():\n    return 3\n")

    after = build_version_fingerprints(_with_file("src/package/helpers.py", changed))

    assert after.root != before.root
    assert _folder(after, "src") != _folder(before, "src")
    assert _folder(after, "src/package") != _folder(before, "src/package")
    assert after.files[0] == before.files[0]
    assert after.files[1] == before.files[1]


def test_a_change_in_one_folder_leaves_a_sibling_folder_unchanged():
    files = [*FILES, PathFingerprint("docs/conf.py", SETUP_FINGERPRINT)]
    before = build_version_fingerprints(files)
    changed = fingerprint_bytes(b"changed\n")

    after = build_version_fingerprints(
        [file for file in files if file.path != "src/package/app.py"]
        + [PathFingerprint("src/package/app.py", changed)]
    )

    assert _folder(after, "docs") == _folder(before, "docs")
    assert _folder(after, "src") != _folder(before, "src")


def test_moving_a_file_changes_the_root_even_when_its_bytes_are_the_same():
    before = build_version_fingerprints(FILES)
    moved = [file for file in FILES if file.path != "setup.py"] + [
        PathFingerprint("tools/setup.py", SETUP_FINGERPRINT)
    ]

    after = build_version_fingerprints(moved)

    assert after.root != before.root
    assert _folder(after, "src") == _folder(before, "src")


def test_an_added_or_removed_file_changes_the_root():
    before = build_version_fingerprints(FILES)
    added = build_version_fingerprints([*FILES, PathFingerprint("src/new.py", SETUP_FINGERPRINT)])
    removed = build_version_fingerprints(FILES[:2])

    assert len({before.root, added.root, removed.root}) == 3


def test_a_file_and_a_folder_with_the_same_contents_never_share_a_fingerprint():
    as_file = build_version_fingerprints([PathFingerprint("name", APP_FINGERPRINT)])
    as_folder = build_version_fingerprints([PathFingerprint("name/app.py", APP_FINGERPRINT)])

    assert as_file.root != as_folder.root


def test_names_with_quotes_tabs_or_non_ascii_letters_are_fingerprinted():
    fingerprints = build_version_fingerprints(
        [PathFingerprint('a", "b.py', APP_FINGERPRINT), PathFingerprint("ü\tc.py", APP_FINGERPRINT)]
    )

    expected_root = _sha256_of_entries(
        [["file", 'a", "b.py', APP_FINGERPRINT], ["file", "ü\tc.py", APP_FINGERPRINT]]
    )
    assert fingerprints.root == expected_root


def test_parent_folder_of_a_top_level_file_is_the_root():
    assert parent_folder_of("setup.py") == ROOT_FOLDER_PATH
    assert parent_folder_of("src/package/app.py") == "src/package"
