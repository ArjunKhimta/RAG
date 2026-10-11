"""Write `retrieval/requirements.lock` and `retrieval/requirements-dev.lock`.

Run from the repository root with the virtual environment active, after any change to
`requirements.txt` or `requirements-dev.txt` (install the change and run the tests first):

    python retrieval/scripts/lock_requirements.py

For each requirements file, asks pip to resolve it for Linux on x86_64 and on aarch64 without
installing anything, held to the versions installed in this environment, and writes every
resulting package with its wheel hashes (see `retrieval.requirements_lock`). The container image
installs the first lock and CI the second. Both are written only if both can be locked. Needs
network access to PyPI and no credentials. Exits 0 when the locks are written, and 1 when a
package cannot be locked safely. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from retrieval.config import RETRIEVAL_ROOT
from retrieval.redaction import redact
from retrieval.requirements_lock import (
    LINUX_MACHINES,
    LOCK_TARGETS,
    LockError,
    LockTarget,
    constraints_text,
    lock_header,
    merge_machines,
    pip_dry_run_arguments,
    render_lock,
    require_installed_versions,
    wheels_from_report,
)

EDITABLE_PROJECT_NAME = "retrieval"


def main() -> int:
    installed_versions = _installed_versions()
    locks: list[tuple[LockTarget, str, int]] = []
    try:
        with tempfile.TemporaryDirectory() as scratch:
            scratch_folder = Path(scratch)
            constraints = scratch_folder / "constraints.txt"
            constraints.write_text(constraints_text(installed_versions))
            for target in LOCK_TARGETS:
                requirements = RETRIEVAL_ROOT / target.requirements_name
                wheels_by_machine = {
                    machine: _resolve_for(machine, requirements, constraints, scratch_folder)
                    for machine in LINUX_MACHINES
                }
                packages = merge_machines(wheels_by_machine)
                require_installed_versions(packages, installed_versions)
                lock_text = render_lock(packages, LINUX_MACHINES, lock_header(target))
                locks.append((target, lock_text, len(packages)))
    except (LockError, subprocess.CalledProcessError) as error:
        _print(f"Not locked: {error}")
        return 1
    for target, lock_text, package_count in locks:
        (RETRIEVAL_ROOT / target.lock_name).write_text(lock_text)
        _print(f"Wrote retrieval/{target.lock_name} with {package_count} packages")
    return 0


def _installed_versions() -> dict[str, str]:
    return {
        distribution.metadata["Name"]: distribution.version
        for distribution in metadata.distributions()
        if distribution.metadata["Name"] != EDITABLE_PROJECT_NAME
    }


def _resolve_for(machine: str, requirements: Path, constraints: Path, scratch_folder: Path) -> dict:
    report = scratch_folder / f"report-{requirements.stem}-{machine}.json"
    target = scratch_folder / f"target-{requirements.stem}-{machine}"
    arguments = pip_dry_run_arguments(machine, requirements, constraints, report, target)
    subprocess.run([sys.executable, "-m", "pip", *arguments], check=True)
    return wheels_from_report(json.loads(report.read_text()))


def _print(text: str) -> None:
    print(redact(text))


if __name__ == "__main__":
    sys.exit(main())
