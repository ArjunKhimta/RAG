from __future__ import annotations

import re

from retrieval.config import RETRIEVAL_ROOT
from retrieval.requirements_lock import PYTHON_VERSION

DOCKERFILE = (RETRIEVAL_ROOT / "Dockerfile").read_text()

DOCKERIGNORE = (RETRIEVAL_ROOT / ".dockerignore").read_text()

PINNED_BASE_IMAGE = re.compile(
    r"^FROM python:(?P<version>\d+\.\d+\.\d+)-slim-\w+@sha256:[0-9a-f]{64}$", re.MULTILINE
)


def _instructions(keyword: str) -> list[str]:
    joined = DOCKERFILE.replace("\\\n", " ")
    return [line for line in joined.splitlines() if line.startswith(keyword + " ")]


def test_the_base_image_is_an_exact_release_pinned_by_digest():
    match = PINNED_BASE_IMAGE.search(DOCKERFILE)

    assert match is not None
    assert len(_instructions("FROM")) == 1


def test_the_image_runs_the_python_the_lock_was_made_for():
    version = PINNED_BASE_IMAGE.search(DOCKERFILE).group("version")

    assert version.startswith(PYTHON_VERSION + ".")


def test_packages_come_only_from_the_lock_as_hash_checked_wheels():
    installs = [line for line in _instructions("RUN") if "pip install" in line]

    assert len(installs) == 1
    for option in ["--require-hashes", "--only-binary :all:", "--no-deps", "requirements.lock"]:
        assert option in installs[0]


def test_the_service_runs_as_an_unprivileged_user():
    users = _instructions("USER")

    assert users and users[-1].split()[1] not in {"root", "0"}


def test_only_named_files_reach_the_builder_and_never_an_env_file():
    rules = [line for line in DOCKERIGNORE.splitlines() if line and not line.startswith("#")]

    assert rules[0] == "*"
    assert not any(".env" in rule for rule in rules if rule.startswith("!"))
    assert {rule for rule in rules if rule.startswith("!")} == {
        "!requirements.lock",
        "!gunicorn.conf.py",
        "!src/",
    }
