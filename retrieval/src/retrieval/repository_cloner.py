"""Shallow clones of public, openly licensed GitHub repositories.

Each clone lives in its own version folder, which holds the checkout and a record of it:

    data/repos/<owner>/<repo>/<version>/
        source/          the checked-out files
        metadata.json    commit ID, license, and sizes

A version is a tag, a branch, or a commit ID. All three go through the same path: `git init`, a
depth-1 `git fetch` of that version, then a checkout of what was fetched. The eval can therefore
pin a release tag today and SWE-bench commit IDs later without a second clone path.

The steps, in order:
1. Reuse the version folder if it already exists. Only complete clones ever reach that name.
2. Ask GitHub's API about the repository, without a token, and refuse before downloading if it
   is private, lacks an allowed open-source license, or is too large.
3. Build `source/` and `metadata.json` inside a `.partial-*` folder next to the final one, measure
   the checkout as a size backstop, then rename the whole folder into place in one step. An
   interrupted clone is deleted, or at worst left under a name no version can have.

Git runs isolated from personal and system settings, with no stored credentials and only a short
list of environment variables, so nothing on this machine can grant access to a private
repository or rewrite the URL. Cloning runs none of the repository's code: hooks are not
transferred by a fetch, submodules are not fetched, and Git LFS downloads are switched off. Every
deletion first confirms the folder is inside the repositories directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from retrieval.config import MAX_REPOSITORY_BYTES, REPOSITORIES_DIRECTORY
from retrieval.github_urls import RepositoryReference
from retrieval.licenses import is_allowed_license

GITHUB_API_REPOSITORY_URL = "https://api.github.com/repos/{full_name}"

GITHUB_API_ACCEPT_HEADER = "application/vnd.github+json"

GITHUB_API_USER_AGENT = "retrieval-code-search"

GITHUB_API_TIMEOUT_SECONDS = 10

HTTP_NOT_FOUND = 404

BYTES_PER_KILOBYTE = 1024

GIT_FETCH_TIMEOUT_SECONDS = 300

GIT_COMMAND_TIMEOUT_SECONDS = 30

GIT_DIRECTORY_NAME = ".git"

SOURCE_DIRECTORY_NAME = "source"

METADATA_FILE_NAME = "metadata.json"

PARTIAL_CLONE_PREFIX = ".partial-"

VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

ISOLATED_GIT_OPTIONS = ("-c", "credential.helper=")

ISOLATED_GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_LFS_SKIP_SMUDGE": "1",
}

PASSED_THROUGH_ENVIRONMENT_VARIABLES = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


class RepositoryError(RuntimeError):
    """Base class for every failure to fetch a repository."""


class InvalidVersionError(RepositoryError):
    """Raised when a version is not a plain tag, branch, or commit ID."""


class RepositoryNotFoundError(RepositoryError):
    """Raised when GitHub has no public repository with the given name."""


class RepositoryNotPublicError(RepositoryError):
    """Raised when GitHub reports the repository as private."""


class UnacceptableLicenseError(RepositoryError):
    """Raised when a repository has no license, or one that is not on the allowed list."""


class RepositoryTooLargeError(RepositoryError):
    """Raised when a repository is over the size limit, before or after cloning."""


class CloneFailedError(RepositoryError):
    """Raised when a git command fails or times out."""


class UnsafeDeletionError(RepositoryError):
    """Raised instead of deleting a folder that is not inside the repositories directory."""


@dataclass(frozen=True)
class RepositoryDetails:
    """What GitHub's API reports about a repository before it is downloaded."""

    is_private: bool
    size_bytes: int
    license_spdx_id: str | None
    license_name: str | None


@dataclass(frozen=True)
class CloneMetadata:
    """The record saved as `metadata.json` next to every checkout."""

    repository: str
    version: str
    commit_id: str
    license_spdx_id: str
    license_name: str
    reported_size_bytes: int
    checkout_size_bytes: int
    cloned_at: str


@dataclass(frozen=True)
class ClonedRepository:
    path: Path
    source_path: Path
    metadata: CloneMetadata
    was_reused: bool


def clone_repository(
    reference: RepositoryReference,
    version: str,
    repositories_directory: Path = REPOSITORIES_DIRECTORY,
    max_repository_bytes: int = MAX_REPOSITORY_BYTES,
    fetch_repository_details: Callable[[RepositoryReference], RepositoryDetails] | None = None,
) -> ClonedRepository:
    """Return a checkout of `reference` at `version`, cloning it only if it is not already there.

    `fetch_repository_details` defaults to asking GitHub's API; tests pass a stand-in.
    """
    _require_valid_version(version)
    version_directory = repositories_directory / reference.owner / reference.name / version
    if version_directory.is_dir():
        return ClonedRepository(
            path=version_directory,
            source_path=version_directory / SOURCE_DIRECTORY_NAME,
            metadata=_read_metadata(version_directory),
            was_reused=True,
        )
    details_fetcher = fetch_repository_details or fetch_github_repository_details
    details = details_fetcher(reference)
    _require_public(reference, details)
    _require_allowed_license(reference, details)
    _require_within_size_limit(
        reference, details.size_bytes, max_repository_bytes, "as reported by GitHub"
    )
    metadata = _clone_into_place(
        reference, version, details, version_directory, repositories_directory, max_repository_bytes
    )
    return ClonedRepository(
        path=version_directory,
        source_path=version_directory / SOURCE_DIRECTORY_NAME,
        metadata=metadata,
        was_reused=False,
    )


def fetch_github_repository_details(reference: RepositoryReference) -> RepositoryDetails:
    """Ask GitHub's API, without a token, whether the repository is private, its license, and size.

    The reported size includes history, so it errs high for a shallow clone.
    """
    api_url = GITHUB_API_REPOSITORY_URL.format(full_name=reference.full_name)
    request = urllib.request.Request(
        api_url,
        headers={"Accept": GITHUB_API_ACCEPT_HEADER, "User-Agent": GITHUB_API_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=GITHUB_API_TIMEOUT_SECONDS) as response:
            repository_json = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == HTTP_NOT_FOUND:
            message = f"{reference.full_name} was not found on GitHub, or it is private"
            raise RepositoryNotFoundError(message) from error
        message = f"GitHub API returned HTTP {error.code} for {reference.full_name}"
        raise RepositoryError(message) from error
    except urllib.error.URLError as error:
        raise RepositoryError(f"GitHub API could not be reached: {error.reason}") from error
    return _repository_details_from_json(repository_json)


def remove_directory_inside(directory: Path, root: Path) -> None:
    """Delete `directory`, but only if it sits strictly inside `root` and is not a symlink."""
    if directory.is_symlink():
        raise UnsafeDeletionError(f"Refusing to delete {directory}: it is a symlink")
    resolved_directory = directory.resolve()
    resolved_root = root.resolve()
    is_inside_root = resolved_directory.is_relative_to(resolved_root)
    is_root_itself = resolved_directory == resolved_root
    if not is_inside_root or is_root_itself:
        raise UnsafeDeletionError(f"Refusing to delete {directory}: it is not inside {root}")
    shutil.rmtree(resolved_directory)


def _repository_details_from_json(repository_json: dict) -> RepositoryDetails:
    license_json = repository_json.get("license") or {}
    size_in_kilobytes = repository_json["size"]
    return RepositoryDetails(
        is_private=repository_json["private"],
        size_bytes=size_in_kilobytes * BYTES_PER_KILOBYTE,
        license_spdx_id=license_json.get("spdx_id"),
        license_name=license_json.get("name"),
    )


def _clone_into_place(
    reference: RepositoryReference,
    version: str,
    details: RepositoryDetails,
    version_directory: Path,
    repositories_directory: Path,
    max_repository_bytes: int,
) -> CloneMetadata:
    version_directory.parent.mkdir(parents=True, exist_ok=True)
    partial_directory = Path(
        tempfile.mkdtemp(prefix=PARTIAL_CLONE_PREFIX, dir=version_directory.parent)
    )
    source_directory = partial_directory / SOURCE_DIRECTORY_NAME
    try:
        source_directory.mkdir()
        _fetch_version(source_directory, reference.clone_url, version)
        checkout_bytes = _measure_checkout_bytes(source_directory)
        _require_within_size_limit(reference, checkout_bytes, max_repository_bytes, "on disk")
        metadata = CloneMetadata(
            repository=reference.full_name,
            version=version,
            commit_id=_read_commit_id(source_directory),
            license_spdx_id=details.license_spdx_id,
            license_name=details.license_name or details.license_spdx_id,
            reported_size_bytes=details.size_bytes,
            checkout_size_bytes=checkout_bytes,
            cloned_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        _write_metadata(partial_directory, metadata)
        partial_directory.rename(version_directory)
    except BaseException:
        remove_directory_inside(partial_directory, repositories_directory)
        raise
    return metadata


def _write_metadata(version_directory: Path, metadata: CloneMetadata) -> None:
    metadata_text = json.dumps(asdict(metadata), indent=2)
    (version_directory / METADATA_FILE_NAME).write_text(metadata_text + "\n")


def _read_metadata(version_directory: Path) -> CloneMetadata:
    metadata_path = version_directory / METADATA_FILE_NAME
    try:
        metadata_json = json.loads(metadata_path.read_text())
        return CloneMetadata(**metadata_json)
    except (OSError, ValueError, TypeError) as error:
        message = (
            f"{version_directory} has no readable {METADATA_FILE_NAME}; "
            "delete the folder and clone again"
        )
        raise RepositoryError(message) from error


def _fetch_version(directory: Path, clone_url: str, version: str) -> None:
    _run_git_in(directory, "init", "--quiet")
    _run_git_in(
        directory,
        "fetch",
        "--quiet",
        "--depth",
        "1",
        "--no-tags",
        clone_url,
        version,
        timeout_seconds=GIT_FETCH_TIMEOUT_SECONDS,
    )
    _run_git_in(directory, "checkout", "--quiet", "--detach", "FETCH_HEAD")


def _read_commit_id(directory: Path) -> str:
    return _run_git_in(directory, "rev-parse", "HEAD")


def _run_git_in(
    directory: Path, *arguments: str, timeout_seconds: int = GIT_COMMAND_TIMEOUT_SECONDS
) -> str:
    """Run isolated git in `directory` with an argument list, never a shell, and return stdout."""
    command = ["git", *ISOLATED_GIT_OPTIONS, "-C", str(directory), *arguments]
    subcommand = arguments[0]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=_git_environment(),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        message = f"git {subcommand} timed out after {timeout_seconds} seconds"
        raise CloneFailedError(message) from error
    if completed.returncode != 0:
        raise CloneFailedError(f"git {subcommand} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _git_environment() -> dict[str, str]:
    """Build git's environment from a short list instead of inheriting everything.

    Inheriting would pass along variables such as `GIT_ASKPASS`, `SSH_ASKPASS`, and
    `GIT_CONFIG_*`, any of which can supply credentials or rewrite the clone URL.
    """
    environment = {
        name: os.environ[name]
        for name in PASSED_THROUGH_ENVIRONMENT_VARIABLES
        if name in os.environ
    }
    environment.update(ISOLATED_GIT_ENVIRONMENT)
    return environment


def _measure_checkout_bytes(directory: Path) -> int:
    """Total the sizes of checked-out files, skipping `.git` and never following symlinks."""
    total_bytes = 0
    for current_directory, directory_names, file_names in os.walk(directory):
        directory_names[:] = [name for name in directory_names if name != GIT_DIRECTORY_NAME]
        for file_name in file_names:
            file_path = Path(current_directory) / file_name
            total_bytes += file_path.lstat().st_size
    return total_bytes


def _require_valid_version(version: str) -> None:
    """Accept only names that are safe both as a git argument and as a single folder name.

    A leading letter or digit rules out git options such as `--upload-pack=...` and hidden names
    such as `.partial-*`, and the character set rules out `/` and therefore `..` path segments.
    """
    if not VERSION_PATTERN.fullmatch(version):
        raise InvalidVersionError(
            f"{version!r} is not a valid version: use a tag, a branch without '/', or a commit ID"
        )


def _require_public(reference: RepositoryReference, details: RepositoryDetails) -> None:
    if details.is_private:
        raise RepositoryNotPublicError(f"{reference.full_name} is private; only public repos")


def _require_allowed_license(reference: RepositoryReference, details: RepositoryDetails) -> None:
    if details.license_spdx_id is None:
        raise UnacceptableLicenseError(f"{reference.full_name} has no license GitHub can detect")
    if not is_allowed_license(details.license_spdx_id):
        raise UnacceptableLicenseError(
            f"{reference.full_name} is licensed {details.license_spdx_id}, "
            "which is not a recognised open-source license"
        )


def _require_within_size_limit(
    reference: RepositoryReference, size_bytes: int, max_bytes: int, size_source: str
) -> None:
    if size_bytes > max_bytes:
        size_megabytes = size_bytes / BYTES_PER_KILOBYTE / BYTES_PER_KILOBYTE
        limit_megabytes = max_bytes / BYTES_PER_KILOBYTE / BYTES_PER_KILOBYTE
        raise RepositoryTooLargeError(
            f"{reference.full_name} is {size_megabytes:.1f} MB {size_source}, "
            f"over the {limit_megabytes:.0f} MB limit"
        )
