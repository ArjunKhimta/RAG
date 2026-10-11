"""Fingerprints of the files a version indexes, arranged as a hash tree, so a later refresh can
tell what changed without comparing chunks.

Each indexed Python file gets the SHA-256 of the exact bytes the chunker read. Each folder on the
way to an indexed file gets the SHA-256 of its entries: the kind, name, and fingerprint of every
indexed file and folder directly inside it, in name order, written as JSON so no name can blur two
entries together. The root folder's fingerprint therefore covers everything indexed.

This is the idea behind git's own trees (a Merkle tree), built over exactly the files indexed and
the bytes read rather than reusing git's hashes: git's trees also cover documentation and settings
that are never indexed, so an edit to a README would look like a change, and a repository's
line-ending rules can make the checked-out bytes differ from the committed bytes git hashes.

If two versions' roots match, every indexed file is identical. Otherwise, comparing folder
fingerprints from the root down finds what changed, skipping any folder whose fingerprint matches.

Fingerprints describe file contents only. Whether the same contents are indexed the same way also
depends on the indexing code, so stored fingerprints carry `INDEX_FORMAT_VERSION`, to be raised by
any change to the walker, secret scanning, chunking, call graph, or embedding inputs that would
index the same files differently; a refresh across a format change must index everything again.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

ROOT_FOLDER_PATH = ""


class EntryKind(StrEnum):
    FILE = "file"
    FOLDER = "folder"


@dataclass(frozen=True)
class PathFingerprint:
    path: str
    fingerprint: str


@dataclass(frozen=True)
class VersionFingerprints:
    root: str
    folders: tuple[PathFingerprint, ...]
    files: tuple[PathFingerprint, ...]


def fingerprint_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_version_fingerprints(files: Iterable[PathFingerprint]) -> VersionFingerprints:
    """Fingerprint every folder holding an indexed file, deepest first, so each folder's entries
    already include its subfolders' fingerprints."""
    sorted_files = tuple(sorted(files, key=_path_of))
    entries_by_folder: dict[str, list[tuple[str, str, str]]] = {ROOT_FOLDER_PATH: []}
    for file in sorted_files:
        folder_path = parent_folder_of(file.path)
        _add_folder_with_ancestors(folder_path, entries_by_folder)
        entries_by_folder[folder_path].append(
            (EntryKind.FILE.value, name_of(file.path), file.fingerprint)
        )
    folder_fingerprints: dict[str, str] = {}
    for folder_path in sorted(entries_by_folder, key=_depth_of, reverse=True):
        fingerprint = _fingerprint_entries(entries_by_folder[folder_path])
        folder_fingerprints[folder_path] = fingerprint
        if folder_path != ROOT_FOLDER_PATH:
            entries_by_folder[parent_folder_of(folder_path)].append(
                (EntryKind.FOLDER.value, name_of(folder_path), fingerprint)
            )
    folders = tuple(
        PathFingerprint(path=folder_path, fingerprint=folder_fingerprints[folder_path])
        for folder_path in sorted(folder_fingerprints)
    )
    return VersionFingerprints(
        root=folder_fingerprints[ROOT_FOLDER_PATH], folders=folders, files=sorted_files
    )


def parent_folder_of(path: str) -> str:
    parent = PurePosixPath(path).parent.as_posix()
    return ROOT_FOLDER_PATH if parent == "." else parent


def name_of(path: str) -> str:
    return PurePosixPath(path).name


def _add_folder_with_ancestors(
    folder_path: str, entries_by_folder: dict[str, list[tuple[str, str, str]]]
) -> None:
    current_path = folder_path
    while current_path not in entries_by_folder:
        entries_by_folder[current_path] = []
        current_path = parent_folder_of(current_path)


def _fingerprint_entries(entries: list[tuple[str, str, str]]) -> str:
    sorted_entries = sorted(entries, key=_entry_name)
    serialized_entries = json.dumps([list(entry) for entry in sorted_entries])
    return fingerprint_bytes(serialized_entries.encode("ascii"))


def _entry_name(entry: tuple[str, str, str]) -> str:
    return entry[1]


def _depth_of(folder_path: str) -> int:
    if folder_path == ROOT_FOLDER_PATH:
        return 0
    return len(PurePosixPath(folder_path).parts)


def _path_of(file: PathFingerprint) -> str:
    return file.path
