"""Secret scanning tests.

Every fake secret is assembled at runtime from pieces, so no real-looking credential appears in
this repository's source, where GitHub push protection and other scanners would flag it.
"""

from __future__ import annotations

import dataclasses
import socket
from pathlib import Path

import pytest

from retrieval import secret_scanning
from retrieval.chunker import chunk_python_source
from retrieval.redaction import REDACTION_PLACEHOLDER
from retrieval.secret_scanning import (
    RedactedSource,
    SecretFinding,
    UnscannableSourceError,
    scan_and_redact,
)

AWS_ACCESS_KEY = "AKIA" + "Z7QPLM2XK4RTB9WD"

GITHUB_TOKEN = "ghp_" + "a8K2mQ9xLp4Rt7Vn3Bc6Yd1Fg5Hj0Kw2Ez8S"

STRIPE_LIVE_KEY = "sk_" + "live_" + "4eC39HqLyjWDarjtT1zdp7dc"

DATABASE_PASSWORD = "hunter2" + "Xq9"

KEYWORD_PASSWORD = "Tr0ub4dor" + "&3xyzQ"

RANDOM_LOOKING_SECRET = "q8Zr2Lx7" + "Vt4Nb9Km3Pw6Hy1Jd5Gf0Cs8"

PRIVATE_KEY_BEGIN = "-----BEGIN RSA " + "PRIVATE KEY-----"

PRIVATE_KEY_END = "-----END RSA " + "PRIVATE KEY-----"

KEY_MATERIAL_LINES = [
    "MIIEpAIBAAKCAQEA" + "3Tz2mr7SZiAMfQyuvBjM2Qw",
    "qL9f" + "Xb2Hc7Rk4Vt8Nm1Pw5Jy0",
]

LINE_BEFORE = "import os"

LINE_AFTER = "UNRELATED = 1"


@pytest.mark.parametrize(
    ("secret_line", "secret", "expected_line"),
    [
        (
            f'AWS_KEY = "{AWS_ACCESS_KEY}"',
            AWS_ACCESS_KEY,
            f'AWS_KEY = "{REDACTION_PLACEHOLDER}"',
        ),
        (
            f'token = "{GITHUB_TOKEN}"',
            GITHUB_TOKEN,
            f'token = "{REDACTION_PLACEHOLDER}"',
        ),
        (
            f'stripe.api_key = "{STRIPE_LIVE_KEY}"',
            STRIPE_LIVE_KEY,
            f'stripe.api_key = "{REDACTION_PLACEHOLDER}"',
        ),
        (
            f'DATABASE_URL = "postgres://admin:{DATABASE_PASSWORD}@db.example.com/app"',
            DATABASE_PASSWORD,
            f'DATABASE_URL = "postgres://admin:{REDACTION_PLACEHOLDER}@db.example.com/app"',
        ),
        (
            f'password = "{KEYWORD_PASSWORD}"',
            KEYWORD_PASSWORD,
            f'password = "{REDACTION_PLACEHOLDER}"',
        ),
        (
            f'SECRET = "{RANDOM_LOOKING_SECRET}"',
            RANDOM_LOOKING_SECRET,
            f'SECRET = "{REDACTION_PLACEHOLDER}"',
        ),
    ],
)
def test_a_secret_is_replaced_in_place_and_every_other_line_is_unchanged(
    tmp_path, secret_line, secret, expected_line
):
    redacted = _scan(tmp_path, "\n".join([LINE_BEFORE, secret_line, LINE_AFTER, ""]))

    redacted_text = redacted.source.decode("utf-8")
    assert redacted_text.split("\n") == [LINE_BEFORE, expected_line, LINE_AFTER, ""]
    assert secret not in redacted_text
    assert {finding.line_number for finding in redacted.findings} == {2}
    assert redacted.redacted_line_numbers == {2}


def test_a_short_value_is_replaced_only_on_its_flagged_line(tmp_path):
    source_text = 'SECRET_KEY = "dev"\nDEVELOPMENT_MODE = "dev server"\n'

    redacted = _scan(tmp_path, source_text)

    assert redacted.source.decode("utf-8") == (
        f'SECRET_KEY = "{REDACTION_PLACEHOLDER}"\nDEVELOPMENT_MODE = "dev server"\n'
    )


def test_a_private_key_block_is_redacted_through_its_end_marker(tmp_path):
    source_lines = [
        f'KEY = """{PRIVATE_KEY_BEGIN}',
        *KEY_MATERIAL_LINES,
        f'{PRIVATE_KEY_END}"""',
        LINE_AFTER,
        "",
    ]

    redacted = _scan(tmp_path, "\n".join(source_lines))

    assert redacted.source.decode("utf-8").split("\n") == [
        f'KEY = """{REDACTION_PLACEHOLDER}',
        REDACTION_PLACEHOLDER,
        REDACTION_PLACEHOLDER,
        f'{REDACTION_PLACEHOLDER}"""',
        LINE_AFTER,
        "",
    ]
    assert redacted.redacted_line_numbers == {1, 2, 3, 4}


def test_a_private_key_written_on_one_line_is_redacted_between_its_markers(tmp_path):
    escaped_key = "\\n".join([PRIVATE_KEY_BEGIN, *KEY_MATERIAL_LINES, PRIVATE_KEY_END])
    source_text = f'KEY = "{escaped_key}\\n"\n{LINE_AFTER}\n'

    redacted = _scan(tmp_path, source_text)

    assert redacted.source.decode("utf-8") == f'KEY = "{REDACTION_PLACEHOLDER}\\n"\n{LINE_AFTER}\n'


def test_without_an_end_marker_strings_are_redacted_to_the_end_of_the_file_but_code_is_not(
    tmp_path,
):
    source_lines = [
        LINE_BEFORE,
        f'KEY = """{PRIVATE_KEY_BEGIN}',
        *KEY_MATERIAL_LINES,
        '"""',
        LINE_AFTER,
        'LATER = "still in the key\'s reach"',
        "",
    ]

    redacted = _scan(tmp_path, "\n".join(source_lines))

    assert redacted.source.decode("utf-8").split("\n") == [
        LINE_BEFORE,
        f'KEY = """{REDACTION_PLACEHOLDER}',
        REDACTION_PLACEHOLDER,
        REDACTION_PLACEHOLDER,
        '"""',
        LINE_AFTER,
        f'LATER = "{REDACTION_PLACEHOLDER}"',
        "",
    ]


def test_a_private_key_written_in_comments_keeps_the_comment_markers(tmp_path):
    source_lines = [
        f"# {PRIVATE_KEY_BEGIN}",
        *[f"#   {material}" for material in KEY_MATERIAL_LINES],
        f"# {PRIVATE_KEY_END}",
        LINE_AFTER,
        "",
    ]

    redacted = _scan(tmp_path, "\n".join(source_lines))

    assert redacted.source.decode("utf-8").split("\n") == [
        f"# {REDACTION_PLACEHOLDER}",
        f"#   {REDACTION_PLACEHOLDER}",
        f"#   {REDACTION_PLACEHOLDER}",
        f"# {REDACTION_PLACEHOLDER}",
        LINE_AFTER,
        "",
    ]


def test_a_value_that_is_also_a_name_is_replaced_only_inside_the_string(tmp_path):
    redacted = _scan(tmp_path, 'app.secret_key = "secret_key"\n')

    assert redacted.source.decode("utf-8") == f'app.secret_key = "{REDACTION_PLACEHOLDER}"\n'


def test_a_secret_in_a_comment_is_replaced_and_the_comment_marker_kept(tmp_path):
    redacted = _scan(tmp_path, f"{LINE_BEFORE}  # token: {GITHUB_TOKEN}\n")

    assert redacted.source.decode("utf-8") == f"{LINE_BEFORE}  # token: {REDACTION_PLACEHOLDER}\n"


@pytest.mark.parametrize(
    ("secret_line", "expected_line"),
    [
        (
            f'NOTE = "use {GITHUB_TOKEN} for deploys"',
            f'NOTE = "use {REDACTION_PLACEHOLDER} for deploys"',
        ),
        (
            f"# deploy with {STRIPE_LIVE_KEY}, then rotate",
            f"# deploy with {REDACTION_PLACEHOLDER}, then rotate",
        ),
    ],
)
def test_a_rule_that_reports_only_a_prefix_still_redacts_the_whole_token(
    tmp_path, secret_line, expected_line
):
    redacted = _scan(tmp_path, secret_line + "\n")

    assert redacted.source.decode("utf-8") == expected_line + "\n"


def test_a_secret_outside_any_string_or_comment_makes_the_file_unscannable(tmp_path):
    with pytest.raises(UnscannableSourceError, match="outside any string or comment"):
        _scan(tmp_path, f"AWS_KEY = {AWS_ACCESS_KEY}\n")


def test_redaction_never_changes_the_chunk_structure(tmp_path):
    source_text = (
        "class Settings:\n"
        '    SECRET_KEY = "secret_key"\n'
        "\n"
        "    def connect(self, secret_key=None):\n"
        f'        password = "{KEYWORD_PASSWORD}"\n'
        "        return secret_key or password\n"
        "\n"
        "\n"
        "def configure(app):\n"
        '    app.secret_key = "secret_key"\n'
        f'    app.config["TOKEN"] = "{GITHUB_TOKEN}"  # token: {GITHUB_TOKEN}\n'
        "    return app\n"
    )
    source = source_text.encode("utf-8")

    redacted = _scan_bytes(tmp_path, source)

    assert len(redacted.findings) >= 4
    assert _chunk_structure(redacted.source) == _chunk_structure(source)


def _chunk_structure(source: bytes) -> list[tuple[str, str, int, int]]:
    return [
        (chunk.kind, chunk.qualified_name, chunk.start_line, chunk.end_line)
        for chunk in chunk_python_source("pkg/module.py", source)
    ]


def test_windows_line_endings_are_kept(tmp_path):
    source = f'{LINE_BEFORE}\r\npassword = "{KEYWORD_PASSWORD}"\r\n'.encode()

    redacted = _scan_bytes(tmp_path, source)

    expected_source = f'{LINE_BEFORE}\r\npassword = "{REDACTION_PLACEHOLDER}"\r\n'.encode()
    assert redacted.source == expected_source
    assert redacted.redacted_line_numbers == {2}


def test_a_clean_file_comes_back_byte_for_byte_unchanged(tmp_path):
    source = b"def add(first, second):\n    return first + second\n"

    redacted = _scan_bytes(tmp_path, source)

    assert redacted.source == source
    assert redacted.findings == []
    assert redacted.redacted_line_numbers == frozenset()


def test_findings_record_location_and_type_but_never_the_value(tmp_path):
    redacted = _scan(tmp_path, f'AWS_KEY = "{AWS_ACCESS_KEY}"\n')

    field_names = {field.name for field in dataclasses.fields(SecretFinding)}
    assert field_names == {"file_path", "line_number", "secret_type"}
    assert "AWS Access Key" in {finding.secret_type for finding in redacted.findings}
    assert AWS_ACCESS_KEY not in repr(redacted.findings)


def test_invalid_utf8_is_refused_instead_of_silently_passing(tmp_path):
    source = b'password = "caf\xe9"\n'

    with pytest.raises(UnscannableSourceError):
        _scan_bytes(tmp_path, source)


def test_a_lone_carriage_return_is_refused_because_line_numbers_would_disagree(tmp_path):
    source = f'{LINE_BEFORE}\rpassword = "{KEYWORD_PASSWORD}"\n'.encode()

    with pytest.raises(UnscannableSourceError):
        _scan_bytes(tmp_path, source)


def test_a_non_utf8_default_encoding_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        secret_scanning.locale, "getpreferredencoding", lambda do_setlocale: "ISO-8859-1"
    )

    with pytest.raises(UnscannableSourceError):
        _scan(tmp_path, f'password = "{KEYWORD_PASSWORD}"\n')


def test_scanning_makes_no_network_connections(tmp_path, monkeypatch):
    def refuse_network(*arguments, **keyword_arguments):
        raise AssertionError("secret scanning must not reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse_network)
    monkeypatch.setattr(socket, "create_connection", refuse_network)
    monkeypatch.setattr(socket, "getaddrinfo", refuse_network)

    redacted = _scan(tmp_path, f'token = "{GITHUB_TOKEN}"\nAWS_KEY = "{AWS_ACCESS_KEY}"\n')

    assert {finding.line_number for finding in redacted.findings} == {1, 2}


def _scan(tmp_path: Path, source_text: str) -> RedactedSource:
    return _scan_bytes(tmp_path, source_text.encode("utf-8"))


def _scan_bytes(tmp_path: Path, source: bytes) -> RedactedSource:
    file_path = tmp_path / "module.py"
    file_path.write_bytes(source)
    return scan_and_redact(file_path, "pkg/module.py", source)
