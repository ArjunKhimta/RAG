"""Compare a version's earlier fingerprints with a new checkout's, to find which indexed files
were added, changed, or removed.

The walk starts at the root and goes down only where fingerprints differ. A folder whose
fingerprint is the same in both holds exactly the same indexed files, so it is skipped without
looking at anything inside it; if the roots match, nothing indexed changed and the walk stops at
once. Inside a folder whose fingerprint differs, or which exists in only one of the two, its own
files are compared one by one, and each subfolder found in either is visited the same way.

A file with the same bytes at a new path counts as removed from the old path and added at the new
one, since its chunks carry the path.
"""

from __future__ import annotations

from dataclasses import dataclass

from retrieval.fingerprints import ROOT_FOLDER_PATH, VersionFingerprints, parent_folder_of


@dataclass(frozen=True)
class FingerprintComparison:
    added: tuple[str, ...]
    changed: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged_file_count: int
    skipped_folder_count: int
    opened_folder_count: int

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.removed)


@dataclass(frozen=True)
class _FolderIndex:
    folder_fingerprints: dict[str, str]
    files_by_folder: dict[str, dict[str, str]]
    subfolders_by_folder: dict[str, set[str]]

    @classmethod
    def of(cls, fingerprints: VersionFingerprints) -> _FolderIndex:
        folder_fingerprints = {folder.path: folder.fingerprint for folder in fingerprints.folders}
        files_by_folder: dict[str, dict[str, str]] = {}
        for file in fingerprints.files:
            files_by_folder.setdefault(parent_folder_of(file.path), {})[file.path] = (
                file.fingerprint
            )
        subfolders_by_folder: dict[str, set[str]] = {}
        for folder_path in folder_fingerprints:
            if folder_path != ROOT_FOLDER_PATH:
                subfolders_by_folder.setdefault(parent_folder_of(folder_path), set()).add(
                    folder_path
                )
        return cls(folder_fingerprints, files_by_folder, subfolders_by_folder)

    def files_in(self, folder_path: str) -> dict[str, str]:
        return self.files_by_folder.get(folder_path, {})

    def subfolders_of(self, folder_path: str) -> set[str]:
        return self.subfolders_by_folder.get(folder_path, set())


def compare_fingerprints(
    previous: VersionFingerprints, current: VersionFingerprints
) -> FingerprintComparison:
    previous_index = _FolderIndex.of(previous)
    current_index = _FolderIndex.of(current)
    added: list[str] = []
    changed: list[str] = []
    removed: list[str] = []
    skipped_folder_count = 0
    opened_folder_count = 0
    folders_to_visit = [ROOT_FOLDER_PATH]
    while folders_to_visit:
        folder_path = folders_to_visit.pop()
        previous_folder = previous_index.folder_fingerprints.get(folder_path)
        current_folder = current_index.folder_fingerprints.get(folder_path)
        if previous_folder == current_folder:
            skipped_folder_count += 1
            continue
        opened_folder_count += 1
        previous_files = previous_index.files_in(folder_path)
        current_files = current_index.files_in(folder_path)
        for file_path in previous_files.keys() | current_files.keys():
            if file_path not in current_files:
                removed.append(file_path)
            elif file_path not in previous_files:
                added.append(file_path)
            elif previous_files[file_path] != current_files[file_path]:
                changed.append(file_path)
        folders_to_visit.extend(
            previous_index.subfolders_of(folder_path) | current_index.subfolders_of(folder_path)
        )
    return FingerprintComparison(
        added=tuple(sorted(added)),
        changed=tuple(sorted(changed)),
        removed=tuple(sorted(removed)),
        unchanged_file_count=len(current.files) - len(added) - len(changed),
        skipped_folder_count=skipped_folder_count,
        opened_folder_count=opened_folder_count,
    )
