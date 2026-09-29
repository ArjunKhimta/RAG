from __future__ import annotations

import pytest

from retrieval.github_urls import (
    InvalidRepositoryUrlError,
    RepositoryReference,
    parse_github_url,
)

FLASK_REFERENCE = RepositoryReference(owner="pallets", name="flask")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/pallets/flask",
        "https://github.com/pallets/flask/",
        "https://github.com/pallets/flask.git",
        "https://GitHub.com/pallets/flask",
        "  https://github.com/pallets/flask  ",
    ],
)
def test_plain_github_repository_urls_are_accepted(url):
    assert parse_github_url(url) == FLASK_REFERENCE


def test_names_with_dots_dashes_and_underscores_are_accepted():
    reference = parse_github_url("https://github.com/some-org/my_project.v2")

    assert reference == RepositoryReference(owner="some-org", name="my_project.v2")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/pallets/flask",
        "git@github.com:pallets/flask.git",
        "ssh://git@github.com/pallets/flask.git",
        "https://gitlab.com/pallets/flask",
        "https://github.com.evil.example/pallets/flask",
        "https://www.github.com/pallets/flask",
        "https://user:token@github.com/pallets/flask",
        "https://github.com:443/pallets/flask",
        "https://github.com/pallets",
        "https://github.com/pallets/flask/tree/main",
        "https://github.com/pallets/flask?tab=readme",
        "https://github.com/pallets/flask#readme",
        "https://github.com/../etc",
        "https://github.com/pallets/..",
        "https://github.com/pallets/.",
        "https://github.com/-pallets/flask",
        "https://github.com/pallets/fla sk",
        "",
    ],
)
def test_anything_else_is_rejected(url):
    with pytest.raises(InvalidRepositoryUrlError):
        parse_github_url(url)


def test_the_clone_url_is_rebuilt_from_the_validated_parts():
    assert FLASK_REFERENCE.clone_url == "https://github.com/pallets/flask.git"
    assert FLASK_REFERENCE.full_name == "pallets/flask"
