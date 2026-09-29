"""Find the Python files in a checked-out repository and turn them into chunks.

The walk only reads files, and only regular files. Symlinks are skipped rather than followed: a
cloned repository could contain a link to `~/.ssh/id_ed25519`, and whatever is chunked is later
sent to Gemini. Every candidate path is also checked to resolve inside the repository.

Folders that hold tooling, dependencies, or build output are not entered. Python files that are
skipped are reported with the reason, so nothing disappears silently.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path, PurePosixPath

from retrieval.chunker import CodeChunk, chunk_python_source
from retrieval.config import MAX_FILE_BYTES

PYTHON_FILE_SUFFIX = ".py"

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {"venv", "node_modules", "__pycache__", "build", "dist", "site-packages"}
)

HIDDEN_NAME_PREFIX = "."

TEST_DIRECTORY_NAMES = frozenset({"tests", "test"})

TEST_FILE_PREFIX = "test_"

TEST_FILE_SUFFIX = "_test.py"

TEST_SUPPORT_FILE_NAMES = frozenset({"conftest.py"})


class SkipReason(StrEnum):
    EXCLUDED_DIRECTORY = "excluded folder"
    SYMLINK = "symlink"
    NOT_A_REGULAR_FILE = "not a regular file"
    TOO_LARGE = "over the file size limit"
    OUTSIDE_REPOSITORY = "resolves outside the repository"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str


@dataclass(frozen=True)
class SkippedPath:
    relative_path: str
    reason: SkipReason


@dataclass(frozen=True)
class WalkResult:
    files: list[SourceFile]
    skipped: list[SkippedPath]


def find_python_files(repository_root: Path, max_file_bytes: int = MAX_FILE_BYTES) -> WalkResult:
    """Return the Python files under `repository_root` in sorted order, plus what was skipped."""
    resolved_root = repository_root.resolve()
    files: list[SourceFile] = []
    skipped: list[SkippedPath] = []
    for current_directory, directory_names, file_names in os.walk(resolved_root):
        current_path = Path(current_directory)
        directory_names[:] = _directories_to_enter(
            current_path, sorted(directory_names), resolved_root, skipped
        )
        for file_name in sorted(file_names):
            if not file_name.endswith(PYTHON_FILE_SUFFIX):
                continue
            file_path = current_path / file_name
            relative_path = _relative_posix_path(file_path, resolved_root)
            skip_reason = _reason_to_skip_file(file_path, resolved_root, max_file_bytes)
            if skip_reason is None:
                files.append(SourceFile(path=file_path, relative_path=relative_path))
            else:
                skipped.append(SkippedPath(relative_path=relative_path, reason=skip_reason))
    return WalkResult(
        files=sorted(files, key=_relative_path_of),
        skipped=sorted(skipped, key=_relative_path_of),
    )


def chunk_source_files(files: list[SourceFile]) -> list[CodeChunk]:
    """Chunk each file under its repository-relative path, marking chunks from test files."""
    chunks: list[CodeChunk] = []
    for source_file in files:
        source = source_file.path.read_bytes()
        is_test_file = is_test_path(source_file.relative_path)
        for chunk in chunk_python_source(source_file.relative_path, source):
            chunks.append(replace(chunk, is_test_file=is_test_file))
    return chunks


def is_test_path(relative_path: str) -> bool:
    """Return whether a repository-relative path is test code.

    Test code is anything under a `tests/` or `test/` folder, plus `test_*.py`, `*_test.py`, and
    `conftest.py` files anywhere.
    """
    path_parts = PurePosixPath(relative_path).parts
    folder_names = path_parts[:-1]
    file_name = path_parts[-1]
    is_inside_test_folder = any(name in TEST_DIRECTORY_NAMES for name in folder_names)
    has_test_file_name = file_name.startswith(TEST_FILE_PREFIX) or file_name.endswith(
        TEST_FILE_SUFFIX
    )
    is_test_support_file = file_name in TEST_SUPPORT_FILE_NAMES
    return is_inside_test_folder or has_test_file_name or is_test_support_file


def _directories_to_enter(
    current_path: Path,
    directory_names: list[str],
    resolved_root: Path,
    skipped: list[SkippedPath],
) -> list[str]:
    directories_to_enter: list[str] = []
    for directory_name in directory_names:
        directory_path = current_path / directory_name
        relative_path = _relative_posix_path(directory_path, resolved_root)
        if directory_path.is_symlink():
            skipped.append(SkippedPath(relative_path=relative_path, reason=SkipReason.SYMLINK))
        elif _is_excluded_directory_name(directory_name):
            skipped.append(
                SkippedPath(relative_path=relative_path, reason=SkipReason.EXCLUDED_DIRECTORY)
            )
        else:
            directories_to_enter.append(directory_name)
    return directories_to_enter


def _is_excluded_directory_name(directory_name: str) -> bool:
    is_hidden = directory_name.startswith(HIDDEN_NAME_PREFIX)
    return is_hidden or directory_name in EXCLUDED_DIRECTORY_NAMES


def _reason_to_skip_file(
    file_path: Path, resolved_root: Path, max_file_bytes: int
) -> SkipReason | None:
    """Check the file itself with `lstat`, so a symlink is judged as a link, never followed."""
    file_status = file_path.lstat()
    if stat.S_ISLNK(file_status.st_mode):
        return SkipReason.SYMLINK
    if not stat.S_ISREG(file_status.st_mode):
        return SkipReason.NOT_A_REGULAR_FILE
    if not file_path.resolve().is_relative_to(resolved_root):
        return SkipReason.OUTSIDE_REPOSITORY
    if file_status.st_size > max_file_bytes:
        return SkipReason.TOO_LARGE
    return None


def _relative_posix_path(path: Path, resolved_root: Path) -> str:
    return path.relative_to(resolved_root).as_posix()


def _relative_path_of(record: SourceFile | SkippedPath) -> str:
    return record.relative_path
