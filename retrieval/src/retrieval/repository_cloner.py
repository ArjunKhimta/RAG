"""Shallow clones of public, openly licensed GitHub repositories.

Each clone lives in its own version folder, which holds the checkout and a record of it:

    data/repos/<owner>/<repo>/<version>/
        source/          the checked-out files
        metadata.json    commit ID, license, license file, and sizes

A version is a tag, a branch, or a commit ID. All three go through the same path: `git init`, a
depth-1 `git fetch` of that version, then a checkout of what was fetched. The eval can therefore
pin a release tag today and SWE-bench commit IDs later without a second clone path.

The steps, in order:
1. Reuse the version folder if it already exists. Only complete clones ever reach that name.
2. Ask GitHub's API about the repository and refuse before downloading if it is private, lacks
   an allowed open-source license, or is too large.
3. Build `source/` and `metadata.json` inside a `.partial-*` folder next to the final one, measure
   the checkout as a size backstop, find the license file, then rename the whole folder into
   place in one step. An interrupted clone is deleted, or at worst left under a name no version
   can have.

Every answer links to the repository's license file at the indexed commit, so the file is found in
the checkout itself, which is exactly that commit; GitHub's API names the file only as it is
today. It is a regular file at the root named LICENSE, LICENCE, or COPYING, in any letter case,
with no extension or `.txt`, `.md`, or `.rst`, preferring them in that order; a symbolic link does
not count. A checkout without one is refused, since its answers could not link their license. A
clone saved before the path was recorded gets it from its checkout when next reused.

A refresh asks first which commit a tag or branch points to now, with `git ls-remote`, which
downloads nothing; it resolves the name in the order `git fetch` does, so the commit it reports is
the one a clone would get. A commit ID never moves, so it is never looked up. A refresh also names
the commit it expects, and a saved clone at any other commit is deleted and cloned again rather
than reused.

GitHub's API allows 60 calls an hour per address without a token, and a host's servers share a
few outbound addresses with every other customer there (on Render, that limit was found used up
before our first call, 2026-10-12). So the API call, and only that call, sends `GITHUB_API_TOKEN`
when it is set: a fine-grained token, which the service insists on (a classic token can carry
broad powers), meant to have read-only access to public repositories and no permissions, which
gives 5,000 calls an hour. Git never receives it, so cloning stays anonymous, and the private
check above still refuses a private repository. The token is dropped from any redirect that leaves
GitHub's API. If GitHub refuses the token (expired or revoked), the call is made again without it.
A call refused for GitHub's rate limit raises `GitHubRateLimitedError`, which passes with time.

Git runs isolated from personal and system settings, with no stored credentials and only a short
list of environment variables, so nothing on this machine can grant access to a private
repository or rewrite the URL. Cloning runs none of the repository's code: hooks are not
transferred by a fetch, submodules are not fetched, and Git LFS downloads are switched off. Every
deletion first confirms the folder is inside the repositories directory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from retrieval.config import (
    GITHUB_API_TOKEN_VARIABLE,
    GITHUB_FINE_GRAINED_TOKEN_PREFIX,
    MAX_REPOSITORY_BYTES,
    REPOSITORIES_DIRECTORY,
)
from retrieval.github_urls import RepositoryReference
from retrieval.licenses import is_allowed_license
from retrieval.redaction import register_secret

logger = logging.getLogger(__name__)

GITHUB_API_HOST = "api.github.com"

GITHUB_API_REPOSITORY_URL = "https://api.github.com/repos/{full_name}"

AUTHORIZATION_HEADER = "Authorization"

RATE_LIMIT_REMAINING_HEADER = "x-ratelimit-remaining"

RETRY_AFTER_HEADER = "retry-after"

RATE_LIMIT_REASON_TEXT = "rate limit"

GITHUB_RATE_LIMITED_MESSAGE = (
    "GitHub's limit for looking up repositories is used up for now; it resets within the hour"
)

GITHUB_API_ACCEPT_HEADER = "application/vnd.github+json"

GITHUB_API_USER_AGENT = "retrieval-code-search"

GITHUB_API_TIMEOUT_SECONDS = 10

HTTP_NOT_FOUND = 404

HTTP_UNAUTHORIZED = 401

RATE_LIMIT_STATUSES = (403, 429)

BYTES_PER_KILOBYTE = 1024

GIT_FETCH_TIMEOUT_SECONDS = 300

GIT_COMMAND_TIMEOUT_SECONDS = 30

GIT_DIRECTORY_NAME = ".git"

SOURCE_DIRECTORY_NAME = "source"

METADATA_FILE_NAME = "metadata.json"

PARTIAL_CLONE_PREFIX = ".partial-"

LICENSE_FILE_PATTERN = re.compile(r"(LICENSE|LICENCE|COPYING)(\.(txt|md|rst))?", re.IGNORECASE)

LICENSE_FILE_PREFERENCE = ("LICENSE", "LICENCE", "COPYING")

VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")

COMMIT_ID_PATTERN = re.compile(r"[0-9a-f]{40}")

PEELED_TAG_SUFFIX = "^{}"

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


class GitHubRateLimitedError(RepositoryError):
    """Raised when GitHub's API refuses a call for its rate limit; trying later may work."""


class InvalidGitHubTokenError(ValueError):
    """Raised when `GITHUB_API_TOKEN` is not a fine-grained token; never includes the token."""


class UnacceptableLicenseError(RepositoryError):
    """Raised when a repository has no license, or one that is not on the allowed list."""


class MissingLicenseFileError(UnacceptableLicenseError):
    """Raised when a checkout has no license file at its root for answers to link to."""


class VersionNotFoundError(RepositoryError):
    """Raised when a tag or branch no longer exists in the repository on GitHub."""


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
    license_path: str | None = None


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
    expected_commit: str | None = None,
) -> ClonedRepository:
    """Return a checkout of `reference` at `version`, cloning it only if it is not already there.

    `fetch_repository_details` defaults to asking GitHub's API; tests pass a stand-in. With
    `expected_commit`, a saved clone at another commit is deleted and cloned again.
    """
    _require_valid_version(version)
    version_directory = repositories_directory / reference.owner / reference.name / version
    if version_directory.is_dir() and expected_commit is not None:
        saved_commit = _read_metadata(version_directory).commit_id
        if saved_commit != expected_commit:
            remove_directory_inside(version_directory, repositories_directory)
    if version_directory.is_dir():
        return ClonedRepository(
            path=version_directory,
            source_path=version_directory / SOURCE_DIRECTORY_NAME,
            metadata=_metadata_with_license_path(version_directory),
            was_reused=True,
        )
    details_fetcher = fetch_repository_details or partial(
        fetch_github_repository_details, api_token=read_github_api_token()
    )
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


def refreshing_clone(
    reference: RepositoryReference,
    version: str,
    find_commit: Callable[[RepositoryReference, str], str] | None = None,
    clone: Callable[..., ClonedRepository] = clone_repository,
) -> ClonedRepository:
    """Like `clone_repository`, but first asks GitHub where a tag or branch points now, so a saved
    clone of a branch that has moved is replaced rather than indexed again as it was.

    A commit ID never moves, so it is cloned or reused without asking GitHub. `find_commit`
    defaults to `find_remote_commit`; tests pass a stand-in.
    """
    if is_commit_id(version):
        return clone(reference, version)
    commit_finder = find_commit or find_remote_commit
    return clone(reference, version, expected_commit=commit_finder(reference, version))


def is_commit_id(version: str) -> bool:
    return COMMIT_ID_PATTERN.fullmatch(version) is not None


def find_remote_commit(reference: RepositoryReference, version: str) -> str:
    """Return the commit a tag or branch of `reference` points to on GitHub now.

    The name is resolved as `git fetch` resolves it: the name itself, `refs/<name>`, a tag (the
    commit an annotated tag points to), then a branch. Raises `VersionNotFoundError` if none
    exists, and `CloneFailedError` if git fails or times out. `ls-remote` keeps only refs ending in
    a pattern, so the peeled name (`<tag>^{}`, the annotated tag's commit) is asked for as well.
    """
    _require_valid_version(version)
    with tempfile.TemporaryDirectory() as empty_directory:
        listing = _run_git_in(
            Path(empty_directory),
            "ls-remote",
            reference.clone_url,
            version,
            version + PEELED_TAG_SUFFIX,
        )
    commits_by_ref = _commits_by_ref(listing)
    tag_ref = f"refs/tags/{version}"
    candidate_refs = (
        version,
        f"refs/{version}",
        tag_ref + PEELED_TAG_SUFFIX,
        tag_ref,
        f"refs/heads/{version}",
    )
    for ref in candidate_refs:
        if ref in commits_by_ref:
            return commits_by_ref[ref]
    raise VersionNotFoundError(f"{version} is not a tag or branch of {reference.full_name}")


def read_github_api_token() -> str | None:
    """Return `GITHUB_API_TOKEN`, or None when it is unset or empty.

    Anything but a fine-grained token raises `InvalidGitHubTokenError`; a token is registered for
    redaction before it is returned.
    """
    token = os.environ.get(GITHUB_API_TOKEN_VARIABLE, "")
    if not token:
        return None
    if not token.startswith(GITHUB_FINE_GRAINED_TOKEN_PREFIX):
        raise InvalidGitHubTokenError(
            f"{GITHUB_API_TOKEN_VARIABLE} must be a fine-grained token "
            f"(starting with {GITHUB_FINE_GRAINED_TOKEN_PREFIX}) with read-only access to public "
            "repositories and no permissions"
        )
    register_secret(token)
    return token


def fetch_github_repository_details(
    reference: RepositoryReference,
    api_token: str | None = None,
    open_url: Callable[..., Any] | None = None,
) -> RepositoryDetails:
    """Ask GitHub's API whether the repository is private, its license, and size.

    The reported size includes history, so it errs high for a shallow clone. `open_url` defaults
    to an opener that drops the token from redirects leaving GitHub's API; tests pass a stand-in.
    """
    opener = open_url or GITHUB_API_OPENER.open
    try:
        repository_json = _request_repository_json(reference, api_token, opener)
    except urllib.error.HTTPError as error:
        if error.code == HTTP_UNAUTHORIZED and api_token is not None:
            logger.warning(
                "GitHub refused %s (expired or revoked?); asking without it",
                GITHUB_API_TOKEN_VARIABLE,
            )
            return fetch_github_repository_details(reference, open_url=open_url)
        if error.code == HTTP_NOT_FOUND:
            message = f"{reference.full_name} was not found on GitHub, or it is private"
            raise RepositoryNotFoundError(message) from error
        if is_rate_limit_refusal(error):
            raise GitHubRateLimitedError(GITHUB_RATE_LIMITED_MESSAGE) from error
        message = f"GitHub API returned HTTP {error.code} for {reference.full_name}"
        raise RepositoryError(message) from error
    except urllib.error.URLError as error:
        raise RepositoryError(f"GitHub API could not be reached: {error.reason}") from error
    return _repository_details_from_json(repository_json)


def is_rate_limit_refusal(error: urllib.error.HTTPError) -> bool:
    """Tell GitHub's rate-limit refusals (primary or secondary) from other 403s, such as a block."""
    if error.code not in RATE_LIMIT_STATUSES:
        return False
    headers = error.headers or {}
    no_calls_left = headers.get(RATE_LIMIT_REMAINING_HEADER) == "0"
    asked_to_wait = headers.get(RETRY_AFTER_HEADER) is not None
    reason_says_so = RATE_LIMIT_REASON_TEXT in str(error.reason).lower()
    return no_calls_left or asked_to_wait or reason_says_so


class TokenDroppingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects (GitHub's API redirects renamed repositories), keeping the token only
    while the redirect stays on GitHub's API over https."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and not _is_github_api_url(newurl):
            redirected.remove_header(AUTHORIZATION_HEADER)
        return redirected


GITHUB_API_OPENER = urllib.request.build_opener(TokenDroppingRedirectHandler())


def _is_github_api_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme == "https" and parts.hostname == GITHUB_API_HOST


def _request_repository_json(
    reference: RepositoryReference, api_token: str | None, opener: Callable[..., Any]
) -> dict:
    headers = {"Accept": GITHUB_API_ACCEPT_HEADER, "User-Agent": GITHUB_API_USER_AGENT}
    if api_token is not None:
        headers[AUTHORIZATION_HEADER] = f"Bearer {api_token}"
    api_url = GITHUB_API_REPOSITORY_URL.format(full_name=reference.full_name)
    request = urllib.request.Request(api_url, headers=headers)
    with opener(request, timeout=GITHUB_API_TIMEOUT_SECONDS) as response:
        return json.load(response)


def find_license_file(source_directory: Path) -> str:
    """Name the license file at the root of a checkout, or raise `MissingLicenseFileError`."""
    candidates = [
        entry.name
        for entry in source_directory.iterdir()
        if LICENSE_FILE_PATTERN.fullmatch(entry.name) and entry.is_file() and not entry.is_symlink()
    ]
    if not candidates:
        raise MissingLicenseFileError(
            f"{source_directory} has no license file at its root (LICENSE, LICENCE, or COPYING)"
        )
    return min(candidates, key=_license_file_rank)


def remove_directory_inside(directory: Path, root: Path) -> None:
    """Delete `directory`, but only if it sits strictly inside `root` and is not a symlink.

    Folders between it and `root` that the deletion leaves empty are removed too, so a deleted
    checkout leaves no empty owner and repository folders behind.
    """
    if directory.is_symlink():
        raise UnsafeDeletionError(f"Refusing to delete {directory}: it is a symlink")
    resolved_directory = directory.resolve()
    resolved_root = root.resolve()
    is_inside_root = resolved_directory.is_relative_to(resolved_root)
    is_root_itself = resolved_directory == resolved_root
    if not is_inside_root or is_root_itself:
        raise UnsafeDeletionError(f"Refusing to delete {directory}: it is not inside {root}")
    shutil.rmtree(resolved_directory)
    _remove_empty_parents(resolved_directory, resolved_root)


def _remove_empty_parents(directory: Path, root: Path) -> None:
    """Remove empty folders above `directory`, stopping at `root` or the first non-empty one.

    `rmdir` only ever removes an empty folder, so a folder another clone is using stays.
    """
    for parent in directory.parents:
        if parent == root:
            return
        try:
            parent.rmdir()
        except OSError:
            return


def _commits_by_ref(listing: str) -> dict[str, str]:
    commits_by_ref: dict[str, str] = {}
    for line in listing.splitlines():
        commit_id, _, ref = line.partition("\t")
        commits_by_ref[ref] = commit_id
    return commits_by_ref


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
        license_path = find_license_file(source_directory)
        metadata = CloneMetadata(
            repository=reference.full_name,
            version=version,
            commit_id=_read_commit_id(source_directory),
            license_spdx_id=details.license_spdx_id,
            license_name=details.license_name or details.license_spdx_id,
            reported_size_bytes=details.size_bytes,
            checkout_size_bytes=checkout_bytes,
            cloned_at=datetime.now(UTC).isoformat(timespec="seconds"),
            license_path=license_path,
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


def _metadata_with_license_path(version_directory: Path) -> CloneMetadata:
    """Read the saved metadata, adding the license file's path to a clone saved without it."""
    metadata = _read_metadata(version_directory)
    if metadata.license_path is not None:
        return metadata
    license_path = find_license_file(version_directory / SOURCE_DIRECTORY_NAME)
    updated = replace(metadata, license_path=license_path)
    _write_metadata(version_directory, updated)
    return updated


def _license_file_rank(file_name: str) -> tuple[int, str]:
    stem = file_name.split(".")[0].upper()
    return LICENSE_FILE_PREFERENCE.index(stem), file_name


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
