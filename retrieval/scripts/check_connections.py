"""Confirm that MongoDB Atlas and the Gemini API are reachable with the configured credentials.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/check_connections.py

Exits 0 when both services answer, 1 when either fails. Credentials are never displayed: every
detail line has already passed through the redaction module.
"""

from __future__ import annotations

import sys

from retrieval.config import load_environment
from retrieval.connection_checks import CheckResult, run_all_checks
from retrieval.redaction import redact

SERVICE_COLUMN_WIDTH = 10

STATUS_COLUMN_WIDTH = 6


def main() -> int:
    load_environment()
    results = run_all_checks()
    for result in results:
        print(_format_result(result))
    return 0 if all(result.passed for result in results) else 1


def _format_result(result: CheckResult) -> str:
    status_label = "OK" if result.passed else "FAIL"
    service_column = result.service_name.ljust(SERVICE_COLUMN_WIDTH)
    status_column = status_label.ljust(STATUS_COLUMN_WIDTH)
    timing_column = f"{result.elapsed_ms:.0f} ms".rjust(8)
    return f"{service_column}{status_column}{timing_column}  {redact(result.detail)}"


if __name__ == "__main__":
    sys.exit(main())
