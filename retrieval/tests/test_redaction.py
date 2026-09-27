from __future__ import annotations

from retrieval.redaction import (
    MINIMUM_SECRET_LENGTH,
    REDACTION_PLACEHOLDER,
    redact,
    redact_exception,
    register_secret,
)

EXAMPLE_API_KEY = "AIzaSyExampleKeyValueForTests"

EXAMPLE_PASSWORD = "sup3r-s3cret-passw0rd"


def test_registered_secret_is_replaced():
    register_secret(EXAMPLE_API_KEY)

    redacted_text = redact(f"request failed with key {EXAMPLE_API_KEY}")

    assert EXAMPLE_API_KEY not in redacted_text
    assert REDACTION_PLACEHOLDER in redacted_text


def test_uri_credentials_are_stripped_even_when_not_registered():
    unregistered_uri = "mongodb+srv://appuser:hunter2@cluster0.mongodb.net/db"

    redacted_text = redact(f"connection to {unregistered_uri} failed")

    assert "hunter2" not in redacted_text
    assert "appuser" not in redacted_text
    assert "cluster0.mongodb.net" in redacted_text


def test_secret_embedded_in_exception_message_is_removed():
    register_secret(EXAMPLE_PASSWORD)
    error = ValueError(f"authentication failed for password {EXAMPLE_PASSWORD} on retry")

    redacted_text = redact_exception(error)

    assert EXAMPLE_PASSWORD not in redacted_text
    assert "ValueError" in redacted_text


def test_text_without_secrets_is_unchanged():
    register_secret(EXAMPLE_API_KEY)

    assert redact("ping succeeded, server 7.0.14") == "ping succeeded, server 7.0.14"


def test_short_values_are_not_registered():
    short_value = "a" * (MINIMUM_SECRET_LENGTH - 1)
    register_secret(short_value)

    assert redact(f"the word {short_value} appears here") == f"the word {short_value} appears here"


def test_redaction_is_idempotent():
    register_secret(EXAMPLE_API_KEY)
    once_redacted = redact(f"key {EXAMPLE_API_KEY} rejected")

    assert redact(once_redacted) == once_redacted


def test_longer_secrets_are_replaced_before_their_prefixes():
    prefix_secret = "secret-value"
    longer_secret = "secret-value-extended"
    register_secret(prefix_secret)
    register_secret(longer_secret)

    redacted_text = redact(longer_secret)

    assert redacted_text == REDACTION_PLACEHOLDER
