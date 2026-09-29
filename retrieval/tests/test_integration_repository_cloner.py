"""Clone the demo repository from GitHub for real. Excluded from the default run.

    pytest retrieval/tests -m integration
"""

from __future__ import annotations

import re

import pytest

from retrieval.github_urls import parse_github_url
from retrieval.repository_cloner import clone_repository, fetch_github_repository_details

pytestmark = pytest.mark.integration

FLASK_URL = "https://github.com/pallets/flask"

FLASK_RELEASE_TAG = "3.1.3"

COMMIT_ID_PATTERN = re.compile(r"[0-9a-f]{40}")


def test_github_reports_the_demo_repository_as_public_and_bsd_licensed():
    details = fetch_github_repository_details(parse_github_url(FLASK_URL))

    assert not details.is_private
    assert details.license_spdx_id == "BSD-3-Clause"
    assert details.size_bytes > 0


def test_the_demo_repository_is_cloned_at_its_release_tag(tmp_path):
    reference = parse_github_url(FLASK_URL)

    cloned = clone_repository(reference, FLASK_RELEASE_TAG, repositories_directory=tmp_path)

    assert cloned.path == tmp_path / "pallets" / "flask" / FLASK_RELEASE_TAG
    assert (cloned.source_path / "src" / "flask" / "app.py").is_file()
    assert COMMIT_ID_PATTERN.fullmatch(cloned.metadata.commit_id)
    assert cloned.metadata.license_spdx_id == "BSD-3-Clause"
