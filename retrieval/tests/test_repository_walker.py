from __future__ import annotations

import os
from pathlib import Path

import pytest

from retrieval.chunker import ChunkKind
from retrieval.redaction import REDACTION_PLACEHOLDER
from retrieval.repository_walker import (
    SkippedPath,
    SkipReason,
    chunk_source_files,
    find_python_files,
    is_test_path,
)

SMALL_SOURCE = "def run():\n    return 1\n"

FAKE_PASSWORD = "Tr0ub4dor" + "&3xyzQ"


@pytest.fixture
def repository_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    _write(root / "setup.py", SMALL_SOURCE)
    _write(root / "src" / "package" / "__init__.py", "")
    _write(root / "src" / "package" / "app.py", SMALL_SOURCE)
    _write(root / "src" / "package" / "README.md", "not python")
    _write(root / "tests" / "conftest.py", SMALL_SOURCE)
    _write(root / "tests" / "test_app.py", SMALL_SOURCE)
    _write(root / ".github" / "scripts" / "release.py", SMALL_SOURCE)
    _write(root / "venv" / "lib" / "module.py", SMALL_SOURCE)
    _write(root / "src" / "package" / "__pycache__" / "cached.py", SMALL_SOURCE)
    _write(root / "build" / "generated.py", SMALL_SOURCE)
    return root


def test_python_files_are_found_with_sorted_repository_relative_paths(repository_root):
    walk_result = find_python_files(repository_root)

    assert [source_file.relative_path for source_file in walk_result.files] == [
        "setup.py",
        "src/package/__init__.py",
        "src/package/app.py",
        "tests/conftest.py",
        "tests/test_app.py",
    ]


def test_hidden_dependency_and_build_folders_are_skipped_and_reported(repository_root):
    walk_result = find_python_files(repository_root)

    assert walk_result.skipped == [
        SkippedPath(".github", SkipReason.EXCLUDED_DIRECTORY),
        SkippedPath("build", SkipReason.EXCLUDED_DIRECTORY),
        SkippedPath("src/package/__pycache__", SkipReason.EXCLUDED_DIRECTORY),
        SkippedPath("venv", SkipReason.EXCLUDED_DIRECTORY),
    ]


def test_a_symlinked_file_pointing_outside_is_skipped_not_followed(tmp_path, repository_root):
    secret_file = tmp_path / "id_ed25519.py"
    secret_file.write_text("PRIVATE_KEY = 'secret'\n")
    (repository_root / "src" / "package" / "leak.py").symlink_to(secret_file)

    walk_result = find_python_files(repository_root)

    assert SkippedPath("src/package/leak.py", SkipReason.SYMLINK) in walk_result.skipped
    assert "src/package/leak.py" not in _found_paths(walk_result)


def test_a_symlinked_folder_is_skipped_not_entered(tmp_path, repository_root):
    outside_folder = tmp_path / "outside"
    _write(outside_folder / "private.py", SMALL_SOURCE)
    (repository_root / "src" / "linked").symlink_to(outside_folder, target_is_directory=True)

    walk_result = find_python_files(repository_root)

    assert SkippedPath("src/linked", SkipReason.SYMLINK) in walk_result.skipped
    assert not any("private.py" in path for path in _found_paths(walk_result))


def test_a_file_over_the_size_limit_is_skipped_and_reported(repository_root):
    _write(repository_root / "src" / "package" / "generated.py", "x = 1\n" * 100)

    walk_result = find_python_files(repository_root, max_file_bytes=len(SMALL_SOURCE))

    assert SkippedPath("src/package/generated.py", SkipReason.TOO_LARGE) in walk_result.skipped
    assert "src/package/app.py" in _found_paths(walk_result)


def test_a_file_that_is_not_a_regular_file_is_skipped(repository_root):
    named_pipe = repository_root / "src" / "package" / "pipe.py"
    os.mkfifo(named_pipe)

    walk_result = find_python_files(repository_root)

    assert SkippedPath("src/package/pipe.py", SkipReason.NOT_A_REGULAR_FILE) in walk_result.skipped


def test_chunks_carry_relative_paths_and_the_test_file_flag(repository_root):
    walk_result = find_python_files(repository_root)

    chunks = chunk_source_files(walk_result.files).chunks

    flags_by_path = {chunk.file_path: chunk.is_test_file for chunk in chunks}
    assert flags_by_path == {
        "setup.py": False,
        "src/package/app.py": False,
        "tests/conftest.py": True,
        "tests/test_app.py": True,
    }
    assert all(chunk.kind == ChunkKind.FUNCTION for chunk in chunks)


def test_secrets_are_redacted_before_chunking_and_only_covering_chunks_are_marked(
    repository_root,
):
    secret_source = (
        "def connect():\n"
        f'    password = "{FAKE_PASSWORD}"\n'
        "    return password\n"
        "\n"
        "\n"
        "def disconnect():\n"
        "    return None\n"
    )
    _write(repository_root / "src" / "package" / "database.py", secret_source)
    walk_result = find_python_files(repository_root)

    chunking = chunk_source_files(walk_result.files)

    database_path = "src/package/database.py"
    database_chunks = {
        chunk.name: chunk for chunk in chunking.chunks if chunk.file_path == database_path
    }
    assert FAKE_PASSWORD not in database_chunks["connect"].text
    assert f'password = "{REDACTION_PLACEHOLDER}"' in database_chunks["connect"].text
    assert (database_chunks["connect"].start_line, database_chunks["connect"].end_line) == (1, 3)
    assert database_chunks["connect"].contains_redaction
    assert not database_chunks["disconnect"].contains_redaction
    assert [(finding.file_path, finding.line_number) for finding in chunking.secret_findings] == [
        ("src/package/database.py", 2)
    ]
    assert all(FAKE_PASSWORD not in chunk.text for chunk in chunking.chunks)


def test_a_file_that_cannot_be_scanned_is_skipped_not_indexed(repository_root):
    (repository_root / "src" / "package" / "latin1.py").write_bytes(b'NAME = "caf\xe9"\n')
    walk_result = find_python_files(repository_root)

    chunking = chunk_source_files(walk_result.files)

    assert chunking.unscannable == [
        SkippedPath("src/package/latin1.py", SkipReason.NOT_SCANNABLE)
    ]
    assert all(chunk.file_path != "src/package/latin1.py" for chunk in chunking.chunks)


@pytest.mark.parametrize(
    "relative_path",
    [
        "tests/test_app.py",
        "tests/helpers/factories.py",
        "src/package/test/fixtures.py",
        "src/package/test_views.py",
        "src/package/views_test.py",
        "conftest.py",
        "src/conftest.py",
    ],
)
def test_test_code_is_recognised_by_its_path(relative_path):
    assert is_test_path(relative_path)


@pytest.mark.parametrize(
    "relative_path",
    [
        "src/flask/app.py",
        "src/flask/testing.py",
        "examples/tutorial/flaskr/db.py",
        "src/contest.py",
        "src/latest_tests.py",
        "src/tests_helpers/module.py",
    ],
)
def test_source_code_is_not_mistaken_for_test_code(relative_path):
    assert not is_test_path(relative_path)


def _found_paths(walk_result) -> list[str]:
    return [source_file.relative_path for source_file in walk_result.files]


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
