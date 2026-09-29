"""Cloner tests that run real git against local repositories, with no network access.

`LocalSourceReference` behaves like a GitHub reference but points its clone URL at a local
repository, so the code under test runs unchanged. The isolation tests plant personal and system
Git settings that would redirect the clone to a "private" repository, and check that git ignores
them.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from retrieval import repository_cloner
from retrieval.github_urls import RepositoryReference
from retrieval.repository_cloner import (
    CloneFailedError,
    InvalidVersionError,
    RepositoryDetails,
    RepositoryError,
    RepositoryNotPublicError,
    RepositoryTooLargeError,
    UnacceptableLicenseError,
    UnsafeDeletionError,
    clone_repository,
    remove_directory_inside,
)

RELEASE_TAG = "1.0.0"

PUBLIC_FIRST_SOURCE = "def first():\n    return 1\n"

PUBLIC_SECOND_SOURCE = "def second():\n    return 2\n"

PRIVATE_SOURCE = "PRIVATE = True\n"

PUBLIC_BSD_DETAILS = RepositoryDetails(
    is_private=False,
    size_bytes=0,
    license_spdx_id="BSD-3-Clause",
    license_name='BSD 3-Clause "New" or "Revised" License',
)

GIT_IDENTITY_OPTIONS = [
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.com",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
]


@dataclass(frozen=True)
class LocalSourceReference(RepositoryReference):
    source_url: str

    @property
    def clone_url(self) -> str:
        return self.source_url


@pytest.fixture
def public_source(tmp_path: Path) -> Path:
    source_directory = _create_repository(tmp_path / "public-source")
    _commit_file(source_directory, PUBLIC_FIRST_SOURCE, "first")
    _git(source_directory, "tag", RELEASE_TAG)
    _commit_file(source_directory, PUBLIC_SECOND_SOURCE, "second")
    return source_directory


@pytest.fixture
def private_source(tmp_path: Path) -> Path:
    source_directory = _create_repository(tmp_path / "private-source")
    _commit_file(source_directory, PRIVATE_SOURCE, "private")
    _git(source_directory, "tag", RELEASE_TAG)
    return source_directory


@pytest.fixture
def reference(public_source: Path) -> LocalSourceReference:
    return LocalSourceReference(owner="owner", name="project", source_url=public_source.as_uri())


@pytest.fixture
def repositories_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "repos"
    directory.mkdir()
    return directory


def test_a_tag_is_cloned_into_source_beside_its_metadata(
    reference, repositories_directory, public_source
):
    cloned = _clone(reference, repositories_directory, RELEASE_TAG)

    version_directory = repositories_directory / "owner" / "project" / RELEASE_TAG
    assert cloned.path == version_directory
    assert cloned.source_path == version_directory / "source"
    assert sorted(path.name for path in version_directory.iterdir()) == ["metadata.json", "source"]
    assert (cloned.source_path / "app.py").read_text() == PUBLIC_FIRST_SOURCE
    assert cloned.metadata.commit_id == _git(public_source, "rev-parse", RELEASE_TAG)
    assert not cloned.was_reused
    assert _partial_folders(repositories_directory) == []


def test_the_metadata_file_records_the_license_commit_and_sizes(
    reference, repositories_directory
):
    cloned = _clone(reference, repositories_directory, RELEASE_TAG)

    metadata_json = json.loads((cloned.path / "metadata.json").read_text())

    assert metadata_json["repository"] == "owner/project"
    assert metadata_json["version"] == RELEASE_TAG
    assert metadata_json["commit_id"] == cloned.metadata.commit_id
    assert metadata_json["license_spdx_id"] == "BSD-3-Clause"
    assert metadata_json["license_name"] == PUBLIC_BSD_DETAILS.license_name
    assert metadata_json["checkout_size_bytes"] == len(PUBLIC_FIRST_SOURCE)


def test_a_commit_id_can_be_used_as_the_version(reference, repositories_directory, public_source):
    first_commit_id = _git(public_source, "rev-parse", RELEASE_TAG)

    cloned = _clone(reference, repositories_directory, first_commit_id)

    assert cloned.path.name == first_commit_id
    assert cloned.metadata.commit_id == first_commit_id
    assert (cloned.source_path / "app.py").read_text() == PUBLIC_FIRST_SOURCE


def test_an_existing_clone_is_reused_with_its_saved_metadata(reference, repositories_directory):
    first_clone = _clone(reference, repositories_directory, RELEASE_TAG)

    second_clone = clone_repository(
        reference,
        RELEASE_TAG,
        repositories_directory=repositories_directory,
        fetch_repository_details=_fail_if_called,
    )

    assert second_clone.was_reused
    assert second_clone.metadata == first_clone.metadata


def test_a_version_folder_without_metadata_is_reported_not_reused(
    reference, repositories_directory
):
    old_format_directory = repositories_directory / "owner" / "project" / RELEASE_TAG
    old_format_directory.mkdir(parents=True)

    with pytest.raises(RepositoryError, match="metadata.json"):
        clone_repository(
            reference,
            RELEASE_TAG,
            repositories_directory=repositories_directory,
            fetch_repository_details=_fail_if_called,
        )


def test_a_private_repository_is_refused_before_cloning(reference, repositories_directory):
    private_details = RepositoryDetails(
        is_private=True, size_bytes=0, license_spdx_id="MIT", license_name="MIT License"
    )

    with pytest.raises(RepositoryNotPublicError):
        _clone(reference, repositories_directory, RELEASE_TAG, details=private_details)

    assert list(repositories_directory.iterdir()) == []


@pytest.mark.parametrize("license_spdx_id", [None, "NOASSERTION", "CC-BY-4.0", "WTFPL"])
def test_a_repository_without_an_allowed_license_is_refused_before_cloning(
    reference, repositories_directory, license_spdx_id
):
    unlicensed_details = RepositoryDetails(
        is_private=False, size_bytes=0, license_spdx_id=license_spdx_id, license_name=None
    )

    with pytest.raises(UnacceptableLicenseError):
        _clone(reference, repositories_directory, RELEASE_TAG, details=unlicensed_details)

    assert list(repositories_directory.iterdir()) == []


@pytest.mark.parametrize("license_spdx_id", ["GPL-3.0", "AGPL-3.0", "CC0-1.0", "Unlicense"])
def test_copyleft_and_public_domain_licenses_are_accepted(
    reference, repositories_directory, license_spdx_id
):
    licensed_details = RepositoryDetails(
        is_private=False, size_bytes=0, license_spdx_id=license_spdx_id, license_name=None
    )

    cloned = _clone(reference, repositories_directory, RELEASE_TAG, details=licensed_details)

    assert cloned.metadata.license_spdx_id == license_spdx_id
    assert cloned.metadata.license_name == license_spdx_id


def test_a_repository_github_reports_as_too_large_is_refused_before_cloning(
    reference, repositories_directory
):
    large_details = RepositoryDetails(
        is_private=False, size_bytes=1001, license_spdx_id="MIT", license_name="MIT License"
    )

    with pytest.raises(RepositoryTooLargeError):
        clone_repository(
            reference,
            RELEASE_TAG,
            repositories_directory=repositories_directory,
            max_repository_bytes=1000,
            fetch_repository_details=_details(large_details),
        )

    assert list(repositories_directory.iterdir()) == []


def test_a_checkout_too_large_on_disk_is_deleted(reference, repositories_directory):
    with pytest.raises(RepositoryTooLargeError):
        clone_repository(
            reference,
            RELEASE_TAG,
            repositories_directory=repositories_directory,
            max_repository_bytes=len(PUBLIC_FIRST_SOURCE) - 1,
            fetch_repository_details=_details(PUBLIC_BSD_DETAILS),
        )

    assert not _version_folder(repositories_directory, RELEASE_TAG).exists()
    assert _partial_folders(repositories_directory) == []


def test_a_failed_fetch_leaves_nothing_behind(reference, repositories_directory):
    with pytest.raises(CloneFailedError):
        _clone(reference, repositories_directory, "9.9.9")

    assert not _version_folder(repositories_directory, "9.9.9").exists()
    assert _partial_folders(repositories_directory) == []


def test_an_interrupted_clone_is_deleted_and_never_reused(
    reference, repositories_directory, monkeypatch
):
    def interrupt(directory: Path) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(repository_cloner, "_measure_checkout_bytes", interrupt)

    with pytest.raises(KeyboardInterrupt):
        _clone(reference, repositories_directory, RELEASE_TAG)

    assert not _version_folder(repositories_directory, RELEASE_TAG).exists()
    assert _partial_folders(repositories_directory) == []


def test_a_leftover_partial_folder_is_ignored(reference, repositories_directory):
    leftover_directory = repositories_directory / "owner" / "project" / ".partial-leftover"
    leftover_directory.mkdir(parents=True)

    cloned = _clone(reference, repositories_directory, RELEASE_TAG)

    assert not cloned.was_reused
    assert (cloned.source_path / "app.py").read_text() == PUBLIC_FIRST_SOURCE


def test_personal_and_system_git_settings_cannot_redirect_the_clone(
    tmp_path, monkeypatch, reference, repositories_directory, public_source, private_source
):
    redirect_to_private = (
        f'[url "{private_source.as_uri()}"]\n\tinsteadOf = {public_source.as_uri()}\n'
    )
    personal_home = tmp_path / "home"
    personal_home.mkdir()
    (personal_home / ".gitconfig").write_text(redirect_to_private)
    system_config = tmp_path / "system-gitconfig"
    system_config.write_text(redirect_to_private)
    monkeypatch.setenv("HOME", str(personal_home))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system_config))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(personal_home / ".gitconfig"))
    _assert_plain_git_follows_the_redirect(tmp_path, public_source)

    cloned = _clone(reference, repositories_directory, RELEASE_TAG)

    assert (cloned.source_path / "app.py").read_text() == PUBLIC_FIRST_SOURCE


def test_git_sees_no_settings_but_the_cleared_credential_helper(tmp_path, monkeypatch):
    personal_home = tmp_path / "home"
    personal_home.mkdir()
    (personal_home / ".gitconfig").write_text("[credential]\n\thelper = store\n")
    monkeypatch.setenv("HOME", str(personal_home))
    monkeypatch.setenv("GIT_ASKPASS", "/bin/echo")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "credential.helper")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "osxkeychain")

    visible_settings = repository_cloner._run_git_in(tmp_path, "config", "--list", "--show-origin")

    assert visible_settings.splitlines() == ["command line:\tcredential.helper="]
    assert "GIT_ASKPASS" not in repository_cloner._git_environment()


@pytest.mark.parametrize(
    "version",
    [
        "",
        "--upload-pack=touch /tmp/pwned",
        "-v",
        ".partial-x",
        "..",
        "../escape",
        "release/3.1",
        "a b",
    ],
)
def test_unsafe_versions_are_refused_before_anything_runs(
    reference, repositories_directory, version
):
    with pytest.raises(InvalidVersionError):
        clone_repository(
            reference,
            version,
            repositories_directory=repositories_directory,
            fetch_repository_details=_fail_if_called,
        )

    assert list(repositories_directory.iterdir()) == []


def test_deletion_inside_the_root_is_allowed(repositories_directory):
    inner_directory = repositories_directory / "owner" / "project" / RELEASE_TAG
    inner_directory.mkdir(parents=True)

    remove_directory_inside(inner_directory, repositories_directory)

    assert not inner_directory.exists()


def test_deletion_outside_the_root_is_refused(tmp_path, repositories_directory):
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    escaping_path = repositories_directory / ".." / "outside"

    with pytest.raises(UnsafeDeletionError):
        remove_directory_inside(outside_directory, repositories_directory)
    with pytest.raises(UnsafeDeletionError):
        remove_directory_inside(escaping_path, repositories_directory)
    with pytest.raises(UnsafeDeletionError):
        remove_directory_inside(repositories_directory, repositories_directory)

    assert outside_directory.exists()
    assert repositories_directory.exists()


def test_deletion_through_a_symlink_is_refused(tmp_path, repositories_directory):
    outside_directory = tmp_path / "outside"
    outside_directory.mkdir()
    symlink_inside_root = repositories_directory / "link"
    symlink_inside_root.symlink_to(outside_directory)

    with pytest.raises(UnsafeDeletionError):
        remove_directory_inside(symlink_inside_root, repositories_directory)

    assert outside_directory.exists()


def _clone(
    reference: RepositoryReference,
    repositories_directory: Path,
    version: str,
    details: RepositoryDetails = PUBLIC_BSD_DETAILS,
) -> repository_cloner.ClonedRepository:
    return clone_repository(
        reference,
        version,
        repositories_directory=repositories_directory,
        fetch_repository_details=_details(details),
    )


def _details(details: RepositoryDetails):
    def fetch(reference: RepositoryReference) -> RepositoryDetails:
        return details

    return fetch


def _fail_if_called(reference: RepositoryReference) -> RepositoryDetails:
    raise AssertionError("GitHub should not have been asked about the repository")


def _assert_plain_git_follows_the_redirect(tmp_path: Path, public_source: Path) -> None:
    """Prove the planted settings work on ordinary git, so the isolation test is meaningful."""
    plain_clone = tmp_path / "plain-clone"
    subprocess.run(
        ["git", "clone", "--quiet", public_source.as_uri(), str(plain_clone)],
        check=True,
        capture_output=True,
    )
    assert (plain_clone / "app.py").read_text() == PRIVATE_SOURCE


def _version_folder(repositories_directory: Path, version: str) -> Path:
    return repositories_directory / "owner" / "project" / version


def _partial_folders(repositories_directory: Path) -> list[Path]:
    return sorted(repositories_directory.rglob(".partial-*"))


def _create_repository(directory: Path) -> Path:
    directory.mkdir()
    _git(directory, "init", "--quiet")
    return directory


def _commit_file(directory: Path, content: str, message: str) -> None:
    (directory / "app.py").write_text(content)
    _git(directory, "add", "app.py")
    _git(directory, "commit", "--quiet", "-m", message)


def _git(directory: Path, *arguments: str) -> str:
    command = ["git", *GIT_IDENTITY_OPTIONS, "-C", str(directory), *arguments]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return completed.stdout.strip()
