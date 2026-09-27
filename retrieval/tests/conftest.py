from __future__ import annotations

from collections.abc import Iterator

import pytest

from retrieval.redaction import clear_registered_secrets


@pytest.fixture(autouse=True)
def isolated_secret_registry() -> Iterator[None]:
    """Keep the process-wide secret registry from leaking between tests."""
    clear_registered_secrets()
    yield
    clear_registered_secrets()
