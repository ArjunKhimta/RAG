"""Build the lock files: every package an install needs, with its hashes.

Two locks are made the same way: `requirements.lock` from `requirements.txt`, which the container
image installs, and `requirements-dev.lock` from `requirements-dev.txt` (the same packages plus
the test and lint tools), which CI installs.

`requirements.txt` pins only the packages the code imports; the packages those need are left to
pip. The image must install exactly what the tests ran against, so the lock lists every package,
direct or not, at the version installed in the development environment, with the SHA-256 of the
wheel for each Linux machine the image is built for: x86_64 (Render) and aarch64 (a local engine
on an Apple Silicon Mac). pip then installs with `--require-hashes`, refusing any file whose bytes
differ from the lock and any package the lock does not list.

The packages are found by asking pip to resolve `requirements.txt` for each machine without
installing anything (`pip install --dry-run --report`), constrained to the installed versions.
pip reads only each package's metadata from PyPI, and the report gives the chosen wheel's file
name and hash. A package resolved to a version other than the installed one, one installed from
something other than a wheel, or one without a SHA-256 is refused rather than locked, as are two
machines disagreeing on a version. A package needed on only one machine is locked with a marker
naming that machine.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LINUX_MACHINES = ("x86_64", "aarch64")

PYTHON_VERSION = "3.12"

MANYLINUX_GLIBC_MINOR_VERSIONS = (17, 28, 34, 39)

ACCEPTED_ABIS = ("cp312", "abi3", "none")

WHEEL_SUFFIX = ".whl"

HASH_ALGORITHM = "sha256"


@dataclass(frozen=True)
class LockTarget:
    requirements_name: str
    lock_name: str
    installed_by: str


LOCK_TARGETS = (
    LockTarget("requirements.txt", "requirements.lock", "the retrieval service's container image"),
    LockTarget("requirements-dev.txt", "requirements-dev.lock", "CI, to run the tests and lints"),
)


class LockError(ValueError):
    """Raised when the resolved packages cannot be locked safely."""


@dataclass(frozen=True)
class ResolvedWheel:
    name: str
    version: str
    file_name: str
    sha256: str


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    hashes: tuple[str, ...]
    machines: tuple[str, ...]


def canonical_name(name: str) -> str:
    """The name pip and PyPI treat as the same package (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def pip_dry_run_arguments(
    machine: str, requirements: Path, constraints: Path, report: Path, target: Path
) -> list[str]:
    platforms = [f"manylinux_2_{minor}_{machine}" for minor in MANYLINUX_GLIBC_MINOR_VERSIONS] + [
        f"manylinux2014_{machine}"
    ]
    arguments = [
        "install",
        "--dry-run",
        "--ignore-installed",
        "--quiet",
        "--report",
        str(report),
        "--only-binary",
        ":all:",
        "--python-version",
        PYTHON_VERSION,
        "--implementation",
        "cp",
    ]
    for abi in ACCEPTED_ABIS:
        arguments += ["--abi", abi]
    for platform in platforms:
        arguments += ["--platform", platform]
    return arguments + [
        "--target",
        str(target),
        "--requirement",
        str(requirements),
        "--constraint",
        str(constraints),
    ]


def constraints_text(installed_versions: Mapping[str, str]) -> str:
    return "".join(f"{name}=={version}\n" for name, version in sorted(installed_versions.items()))


def wheels_from_report(report: Mapping[str, Any]) -> dict[str, ResolvedWheel]:
    """The wheel pip chose for each package, keyed by canonical name."""
    wheels: dict[str, ResolvedWheel] = {}
    for item in report["install"]:
        metadata = item["metadata"]
        name = canonical_name(metadata["name"])
        download = item["download_info"]
        file_name = download["url"].rsplit("/", 1)[-1]
        if not file_name.endswith(WHEEL_SUFFIX):
            raise LockError(f"{name} would be installed from {file_name}, not a wheel")
        hashes = download.get("archive_info", {}).get("hashes", {})
        if HASH_ALGORITHM not in hashes:
            raise LockError(f"{name} {metadata['version']} has no {HASH_ALGORITHM} hash")
        wheels[name] = ResolvedWheel(
            name=name,
            version=metadata["version"],
            file_name=file_name,
            sha256=hashes[HASH_ALGORITHM],
        )
    return wheels


def merge_machines(
    wheels_by_machine: Mapping[str, Mapping[str, ResolvedWheel]],
) -> list[LockedPackage]:
    names = sorted({name for wheels in wheels_by_machine.values() for name in wheels})
    packages = []
    for name in names:
        found = {
            machine: wheels[name] for machine, wheels in wheels_by_machine.items() if name in wheels
        }
        versions = {wheel.version for wheel in found.values()}
        if len(versions) > 1:
            raise LockError(f"{name} resolves to different versions: {', '.join(sorted(versions))}")
        packages.append(
            LockedPackage(
                name=name,
                version=versions.pop(),
                hashes=tuple(sorted({wheel.sha256 for wheel in found.values()})),
                machines=tuple(machine for machine in wheels_by_machine if machine in found),
            )
        )
    return packages


def require_installed_versions(
    packages: Iterable[LockedPackage], installed_versions: Mapping[str, str]
) -> None:
    """Refuse any package the tests did not run against at that exact version."""
    installed = {canonical_name(name): version for name, version in installed_versions.items()}
    for package in packages:
        tested_version = installed.get(package.name)
        if tested_version is None:
            raise LockError(f"{package.name} is not installed in the development environment")
        if tested_version != package.version:
            raise LockError(
                f"{package.name} resolves to {package.version}, "
                f"but {tested_version} is installed and tested"
            )


def lock_header(target: LockTarget) -> str:
    return (
        f"# Every package {target.installed_by} installs, with the SHA-256 of each\n"
        f"# Linux wheel (x86_64 and aarch64). Generated from {target.requirements_name} at the\n"
        "# versions installed in the development environment; do not edit by hand. Regenerate\n"
        "# with: python retrieval/scripts/lock_requirements.py\n"
    )


def render_lock(
    packages: Iterable[LockedPackage], all_machines: tuple[str, ...], header: str
) -> str:
    entries = [_render_package(package, all_machines) for package in packages]
    return header + "".join(entries)


def _render_package(package: LockedPackage, all_machines: tuple[str, ...]) -> str:
    requirement = f"{package.name}=={package.version}"
    if package.machines != all_machines:
        machine_conditions = " or ".join(
            f'platform_machine == "{machine}"' for machine in package.machines
        )
        requirement += f" ; {machine_conditions}"
    hash_lines = "".join(f" \\\n    --hash={HASH_ALGORITHM}:{sha256}" for sha256 in package.hashes)
    return f"{requirement}{hash_lines}\n"


def locked_versions(lock_text: str) -> dict[str, str]:
    """Package versions named in a lock file, for checking it against `requirements.txt`."""
    pattern = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.MULTILINE)
    return {canonical_name(name): version for name, version in pattern.findall(lock_text)}
