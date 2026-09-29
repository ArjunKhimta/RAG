from __future__ import annotations

import pytest

from retrieval.licenses import is_allowed_license


@pytest.mark.parametrize(
    "spdx_id",
    ["MIT", "BSD-3-Clause", "Apache-2.0", "GPL-3.0", "GPL-3.0-only", "AGPL-3.0", "LGPL-2.1",
     "MPL-2.0", "Unlicense", "CC0-1.0"],
)
def test_osi_approved_licenses_and_the_chosen_public_domain_dedications_are_allowed(spdx_id):
    assert is_allowed_license(spdx_id)


@pytest.mark.parametrize(
    "spdx_id",
    [None, "", "NOASSERTION", "CC-BY-4.0", "CC-BY-SA-4.0", "WTFPL", "BSD-4-Clause", "mit"],
)
def test_missing_unrecognised_and_non_osi_licenses_are_refused(spdx_id):
    assert not is_allowed_license(spdx_id)
