from __future__ import annotations

from pathlib import Path

import pytest

from retrieval.config import RETRIEVAL_ROOT
from retrieval.requirements_lock import (
    LINUX_MACHINES,
    LOCK_TARGETS,
    LockedPackage,
    LockError,
    canonical_name,
    locked_versions,
    merge_machines,
    pip_dry_run_arguments,
    render_lock,
    require_installed_versions,
    wheels_from_report,
)

HEADER = "# test lock\n"


def _report_item(name, version, file_name, sha256="a" * 64):
    hashes = {} if sha256 is None else {"sha256": sha256}
    return {
        "metadata": {"name": name, "version": version},
        "download_info": {
            "url": f"https://files.pythonhosted.org/packages/xx/{file_name}",
            "archive_info": {"hashes": hashes},
        },
    }


def test_the_chosen_wheel_and_its_hash_are_read_from_the_report():
    report = {"install": [_report_item("Flask", "3.1.3", "flask-3.1.3-py3-none-any.whl")]}

    wheels = wheels_from_report(report)

    assert wheels["flask"].version == "3.1.3"
    assert wheels["flask"].sha256 == "a" * 64


def test_a_package_that_would_be_built_from_source_is_refused():
    report = {"install": [_report_item("thing", "1.0", "thing-1.0.tar.gz")]}

    with pytest.raises(LockError, match="not a wheel"):
        wheels_from_report(report)


def test_a_package_without_a_sha256_is_refused():
    report = {"install": [_report_item("thing", "1.0", "thing-1.0-py3-none-any.whl", None)]}

    with pytest.raises(LockError, match="no sha256"):
        wheels_from_report(report)


def _wheels(name, version, sha256):
    return wheels_from_report({"install": [_report_item(name, version, f"{name}.whl", sha256)]})


def test_each_machines_wheel_hash_is_kept():
    packages = merge_machines(
        {
            "x86_64": _wheels("numpy", "2.5.3", "1" * 64),
            "aarch64": _wheels("numpy", "2.5.3", "2" * 64),
        }
    )

    assert packages == [LockedPackage("numpy", "2.5.3", ("1" * 64, "2" * 64), LINUX_MACHINES)]


def test_machines_that_disagree_on_a_version_are_refused():
    with pytest.raises(LockError, match="different versions"):
        merge_machines(
            {
                "x86_64": _wheels("numpy", "2.5.3", "1" * 64),
                "aarch64": _wheels("numpy", "2.5.2", "2" * 64),
            }
        )


def test_a_package_needed_on_one_machine_only_carries_a_marker():
    packages = merge_machines({"x86_64": _wheels("only-here", "1.0", "1" * 64), "aarch64": {}})

    lock_text = render_lock(packages, LINUX_MACHINES, HEADER)

    assert 'only-here==1.0 ; platform_machine == "x86_64" \\\n    --hash=sha256:' in lock_text


@pytest.mark.parametrize(
    "installed, message",
    [({"numpy": "2.5.2"}, "but 2.5.2 is installed"), ({}, "not installed")],
)
def test_a_version_the_tests_did_not_run_against_is_refused(installed, message):
    packages = [LockedPackage("numpy", "2.5.3", ("1" * 64,), LINUX_MACHINES)]

    with pytest.raises(LockError, match=message):
        require_installed_versions(packages, installed)


def test_installed_names_match_whatever_their_spelling():
    packages = [LockedPackage("typing-extensions", "4.16.0", ("1" * 64,), LINUX_MACHINES)]

    require_installed_versions(packages, {"typing_extensions": "4.16.0"})


def test_a_rendered_lock_reads_back_its_versions():
    packages = [
        LockedPackage("flask", "3.1.3", ("1" * 64,), LINUX_MACHINES),
        LockedPackage("numpy", "2.5.3", ("2" * 64, "3" * 64), LINUX_MACHINES),
    ]

    assert locked_versions(render_lock(packages, LINUX_MACHINES, HEADER)) == {
        "flask": "3.1.3",
        "numpy": "2.5.3",
    }


def test_pip_resolves_without_installing_for_one_linux_machine(tmp_path: Path):
    arguments = pip_dry_run_arguments(
        "aarch64", tmp_path / "r.txt", tmp_path / "c.txt", tmp_path / "report.json", tmp_path
    )

    assert arguments[:2] == ["install", "--dry-run"]
    assert ["--only-binary", ":all:"] == arguments[arguments.index("--only-binary") :][:2]
    platforms = [arguments[i + 1] for i, value in enumerate(arguments) if value == "--platform"]
    assert platforms and all(platform.endswith("_aarch64") for platform in platforms)


def _pinned_requirements(requirements_file: Path) -> dict[str, str]:
    pins = {}
    for line in requirements_file.read_text().splitlines():
        if line.strip() and not line.startswith(("#", "-")):
            name, version = line.split("==")
            pins[canonical_name(name)] = version.strip()
    return pins


@pytest.mark.parametrize("target", LOCK_TARGETS, ids=lambda target: target.lock_name)
def test_each_lock_holds_every_pinned_requirement_at_its_pinned_version(target):
    locked = locked_versions((RETRIEVAL_ROOT / target.lock_name).read_text())

    for name, version in _pinned_requirements(RETRIEVAL_ROOT / target.requirements_name).items():
        assert locked.get(name) == version, f"{name}: regenerate {target.lock_name}"


def test_the_development_lock_holds_everything_the_image_lock_holds():
    image_lock = locked_versions((RETRIEVAL_ROOT / "requirements.lock").read_text())
    development_lock = locked_versions((RETRIEVAL_ROOT / "requirements-dev.lock").read_text())

    assert image_lock.items() <= development_lock.items()


@pytest.mark.parametrize("target", LOCK_TARGETS, ids=lambda target: target.lock_name)
def test_every_locked_package_carries_a_hash(target):
    entries = (RETRIEVAL_ROOT / target.lock_name).read_text().split("\n")
    requirement_lines = [line for line in entries if "==" in line and not line.startswith("#")]
    hash_lines = [line for line in entries if "--hash=sha256:" in line]

    assert all(line.endswith("\\") for line in requirement_lines)
    assert len(hash_lines) >= len(requirement_lines)
