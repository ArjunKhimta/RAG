"""Scrubbing of secret material from any text that is about to be displayed.

Nothing in this service prints a credential on purpose. The realistic leak is indirect: a
pymongo error message embeds the full connection URI including the password, and an HTTP error
from an API client can echo the key it was sent. Every string that reaches stdout therefore
passes through `redact` first.
"""

from __future__ import annotations

import re

REDACTION_PLACEHOLDER = "***REDACTED***"

MINIMUM_SECRET_LENGTH = 8

URI_CREDENTIALS_PATTERN = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<credentials>[^/\s@]+)@")

_registered_secrets: set[str] = set()


def register_secret(value: str) -> None:
    """Record a value that must never appear in displayed text.

    Values shorter than `MINIMUM_SECRET_LENGTH` are ignored, because replacing a short common
    string would corrupt unrelated output without protecting anything worth protecting.
    """
    if value and len(value) >= MINIMUM_SECRET_LENGTH:
        _registered_secrets.add(value)


def clear_registered_secrets() -> None:
    _registered_secrets.clear()


def redact(text: str) -> str:
    """Return `text` with every registered secret and every embedded URI credential removed."""
    redacted_text = text
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        redacted_text = redacted_text.replace(secret, REDACTION_PLACEHOLDER)
    return URI_CREDENTIALS_PATTERN.sub(_replace_uri_credentials, redacted_text)


def redact_exception(error: Exception) -> str:
    """Return a redacted one-line description of `error`, safe to display."""
    return redact(f"{type(error).__name__}: {error}")


def _replace_uri_credentials(match: re.Match[str]) -> str:
    return f"{match.group('scheme')}{REDACTION_PLACEHOLDER}@"
