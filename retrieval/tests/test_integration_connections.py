"""Checks that reach the real services. Excluded from the default run.

    pytest retrieval/tests -m integration
"""

from __future__ import annotations

import os

import pytest

from retrieval.config import (
    GEMINI_API_KEY_VARIABLE,
    MONGODB_URI_VARIABLE,
    load_environment,
)
from retrieval.connection_checks import check_gemini, check_mongodb

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def loaded_environment():
    load_environment()


def _skip_unless_configured(variable_name: str) -> None:
    if not os.environ.get(variable_name):
        pytest.skip(f"{variable_name} is not configured")


def test_mongodb_is_reachable():
    _skip_unless_configured(MONGODB_URI_VARIABLE)

    result = check_mongodb()

    assert result.passed, result.detail


def test_gemini_is_reachable():
    _skip_unless_configured(GEMINI_API_KEY_VARIABLE)

    result = check_gemini()

    assert result.passed, result.detail
