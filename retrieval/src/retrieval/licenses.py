"""The licenses a repository must have before it can be indexed.

The list holds every license that GitHub can detect and the Open Source Initiative approves. It
was derived from the SPDX license list 3.29.0 (`isOsiApproved`) intersected with GitHub's
choosealicense.com catalogue, and copyleft licenses are included. CC0-1.0 is added by choice: it
is a public-domain dedication that the OSI has not approved.

GitHub's API reports the older SPDX IDs for the GNU licenses, such as `GPL-3.0`, so both those
and the current `-only` forms are listed. GitHub reports `NOASSERTION` for a license file it
cannot identify, which is deliberately not on the list.
"""

from __future__ import annotations

OSI_APPROVED_SPDX_IDS = frozenset(
    {
        "0BSD",
        "AFL-3.0",
        "AGPL-3.0",
        "AGPL-3.0-only",
        "Apache-2.0",
        "Artistic-2.0",
        "BlueOak-1.0.0",
        "BSD-2-Clause",
        "BSD-2-Clause-Patent",
        "BSD-3-Clause",
        "BSL-1.0",
        "CECILL-2.1",
        "CERN-OHL-P-2.0",
        "CERN-OHL-S-2.0",
        "CERN-OHL-W-2.0",
        "ECL-2.0",
        "EPL-1.0",
        "EPL-2.0",
        "EUPL-1.1",
        "EUPL-1.2",
        "GPL-2.0",
        "GPL-2.0-only",
        "GPL-3.0",
        "GPL-3.0-only",
        "ISC",
        "LGPL-2.1",
        "LGPL-2.1-only",
        "LGPL-3.0",
        "LGPL-3.0-only",
        "LPPL-1.3c",
        "MIT",
        "MIT-0",
        "MPL-2.0",
        "MS-PL",
        "MS-RL",
        "MulanPSL-2.0",
        "NCSA",
        "OFL-1.1",
        "OSL-3.0",
        "PostgreSQL",
        "Unlicense",
        "UPL-1.0",
        "Zlib",
    }
)

PUBLIC_DOMAIN_DEDICATION_SPDX_IDS = frozenset({"CC0-1.0"})

ALLOWED_LICENSE_SPDX_IDS = OSI_APPROVED_SPDX_IDS | PUBLIC_DOMAIN_DEDICATION_SPDX_IDS


def is_allowed_license(spdx_id: str | None) -> bool:
    return spdx_id in ALLOWED_LICENSE_SPDX_IDS
