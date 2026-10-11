from __future__ import annotations

import random

from retrieval.fingerprint_comparison import compare_fingerprints
from retrieval.fingerprints import PathFingerprint, build_version_fingerprints, fingerprint_bytes

RANDOM_SEED = 20261011

RANDOM_CASE_COUNT = 300

CANDIDATE_PATHS = [
    "setup.py",
    "src/package/__init__.py",
    "src/package/app.py",
    "src/package/cli.py",
    "src/package/json/__init__.py",
    "src/package/json/tag.py",
    "src/package/sansio/app.py",
    "tests/conftest.py",
    "tests/test_app.py",
    "tests/apps/cliapp/app.py",
    "docs/conf.py",
    "examples/tutorial/flaskr/db.py",
]

ORIGINAL = {
    "setup.py": b"setup\n",
    "src/package/app.py": b"app\n",
    "src/package/cli.py": b"cli\n",
    "src/package/json/tag.py": b"tag\n",
    "tests/test_app.py": b"test\n",
    "docs/conf.py": b"conf\n",
}


def _fingerprints(contents: dict[str, bytes]):
    return build_version_fingerprints(
        PathFingerprint(path, fingerprint_bytes(content)) for path, content in contents.items()
    )


def _with(contents: dict[str, bytes], changes: dict[str, bytes | None]) -> dict[str, bytes]:
    """Return a copy of `contents` with each changed path set, or removed when its value is None."""
    updated = dict(contents)
    for path, content in changes.items():
        if content is None:
            del updated[path]
        else:
            updated[path] = content
    return updated


def _file_by_file_difference(previous: dict[str, bytes], current: dict[str, bytes]) -> tuple:
    added = sorted(current.keys() - previous.keys())
    removed = sorted(previous.keys() - current.keys())
    changed = sorted(
        path for path in previous.keys() & current.keys() if previous[path] != current[path]
    )
    return tuple(added), tuple(changed), tuple(removed)


def test_identical_versions_stop_at_the_root():
    comparison = compare_fingerprints(_fingerprints(ORIGINAL), _fingerprints(ORIGINAL))

    assert not comparison.has_changes
    assert comparison.unchanged_file_count == len(ORIGINAL)
    assert comparison.skipped_folder_count == 1
    assert comparison.opened_folder_count == 0


def test_a_changed_file_opens_only_the_folders_on_its_path():
    current = _with(ORIGINAL, {"src/package/json/tag.py": b"tag changed\n"})

    comparison = compare_fingerprints(_fingerprints(ORIGINAL), _fingerprints(current))

    assert comparison.changed == ("src/package/json/tag.py",)
    assert comparison.added == ()
    assert comparison.removed == ()
    assert comparison.unchanged_file_count == len(ORIGINAL) - 1
    assert comparison.opened_folder_count == 4
    assert comparison.skipped_folder_count == 2


def test_added_and_removed_files_are_found_in_new_and_vanished_folders():
    current = _with(ORIGINAL, {"docs/conf.py": None, "src/package/sansio/app.py": b"sansio\n"})

    comparison = compare_fingerprints(_fingerprints(ORIGINAL), _fingerprints(current))

    assert comparison.added == ("src/package/sansio/app.py",)
    assert comparison.removed == ("docs/conf.py",)
    assert comparison.changed == ()
    assert comparison.unchanged_file_count == len(ORIGINAL) - 1


def test_a_moved_file_is_removed_from_its_old_path_and_added_at_its_new_one():
    current = _with(ORIGINAL, {"setup.py": None, "tools/setup.py": b"setup\n"})

    comparison = compare_fingerprints(_fingerprints(ORIGINAL), _fingerprints(current))

    assert comparison.added == ("tools/setup.py",)
    assert comparison.removed == ("setup.py",)


def test_every_file_removed_and_replaced_is_reported_in_full():
    current = {"other/module.py": b"other\n"}

    comparison = compare_fingerprints(_fingerprints(ORIGINAL), _fingerprints(current))

    assert comparison.added == ("other/module.py",)
    assert comparison.removed == tuple(sorted(ORIGINAL))
    assert comparison.unchanged_file_count == 0


def test_skipping_matching_folders_finds_exactly_what_a_file_by_file_comparison_finds():
    generator = random.Random(RANDOM_SEED)
    contents = [b"one\n", b"two\n", b"three\n"]
    for _ in range(RANDOM_CASE_COUNT):
        previous = {
            path: generator.choice(contents)
            for path in generator.sample(CANDIDATE_PATHS, generator.randint(1, 8))
        }
        current = {
            path: generator.choice(contents)
            for path in generator.sample(CANDIDATE_PATHS, generator.randint(1, 8))
        }
        for path in generator.sample(sorted(previous), generator.randint(0, len(previous))):
            current[path] = previous[path]

        comparison = compare_fingerprints(_fingerprints(previous), _fingerprints(current))

        added, changed, removed = _file_by_file_difference(previous, current)
        assert (comparison.added, comparison.changed, comparison.removed) == (
            added,
            changed,
            removed,
        )
        assert comparison.unchanged_file_count == len(current) - len(added) - len(changed)
