"""Validation of the GitHub URLs users submit for indexing.

Only `https://github.com/<owner>/<repo>` is accepted, optionally ending in `.git` or `/`. The owner
and repository names later become folder names under `data/repos/`, so this strict check is also
what stops a crafted URL such as `https://github.com/../../etc` from escaping that folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

GITHUB_HOST = "github.com"

REQUIRED_SCHEME = "https"

GIT_SUFFIX = ".git"

OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")

REPOSITORY_NAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")

RESERVED_PATH_NAMES = frozenset({".", ".."})


class InvalidRepositoryUrlError(ValueError):
    """Raised when a URL is not a plain `https://github.com/<owner>/<repo>` address."""


@dataclass(frozen=True)
class RepositoryReference:
    owner: str
    name: str

    @property
    def clone_url(self) -> str:
        return f"{REQUIRED_SCHEME}://{GITHUB_HOST}/{self.owner}/{self.name}{GIT_SUFFIX}"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_github_url(url: str) -> RepositoryReference:
    """Return the owner and repository named by `url`, or raise `InvalidRepositoryUrlError`."""
    parts = urlsplit(url.strip())
    if parts.scheme != REQUIRED_SCHEME:
        raise InvalidRepositoryUrlError("Only https:// GitHub URLs are accepted")
    if parts.netloc.lower() != GITHUB_HOST:
        raise InvalidRepositoryUrlError("Only github.com URLs are accepted")
    if parts.query or parts.fragment:
        raise InvalidRepositoryUrlError("The URL must not have a query string or fragment")
    path_names = _split_repository_path(parts.path)
    if len(path_names) != 2:
        raise InvalidRepositoryUrlError("The URL must have the form https://github.com/<owner>/<repo>")
    owner, name = path_names
    _require_valid_owner(owner)
    _require_valid_repository_name(name)
    return RepositoryReference(owner=owner, name=name)


def _split_repository_path(path: str) -> list[str]:
    trimmed_path = path.strip("/")
    trimmed_path = trimmed_path.removesuffix(GIT_SUFFIX)
    return trimmed_path.split("/")


def _require_valid_owner(owner: str) -> None:
    if not OWNER_PATTERN.fullmatch(owner):
        raise InvalidRepositoryUrlError("The owner name is not a valid GitHub user or organization")


def _require_valid_repository_name(name: str) -> None:
    if name in RESERVED_PATH_NAMES or not REPOSITORY_NAME_PATTERN.fullmatch(name):
        raise InvalidRepositoryUrlError("The repository name is not a valid GitHub repository name")
