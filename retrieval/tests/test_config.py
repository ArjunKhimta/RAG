from __future__ import annotations

import pytest

from retrieval.config import (
    GEMINI_API_KEY_VARIABLE,
    MONGODB_URI_VARIABLE,
    MissingConfigError,
    get_required_env,
)
from retrieval.redaction import REDACTION_PLACEHOLDER, redact

VARIABLE_NAME = "TEST_ONLY_VARIABLE"

SECRET_VALUE = "value-that-must-never-be-displayed"


def test_returns_the_value_when_the_variable_is_set(monkeypatch):
    monkeypatch.setenv(VARIABLE_NAME, SECRET_VALUE)

    assert get_required_env(VARIABLE_NAME) == SECRET_VALUE


def test_raises_when_the_variable_is_missing(monkeypatch):
    monkeypatch.delenv(VARIABLE_NAME, raising=False)

    with pytest.raises(MissingConfigError) as raised:
        get_required_env(VARIABLE_NAME)

    assert raised.value.variable_name == VARIABLE_NAME


def test_raises_when_the_variable_is_empty(monkeypatch):
    monkeypatch.setenv(VARIABLE_NAME, "")

    with pytest.raises(MissingConfigError):
        get_required_env(VARIABLE_NAME)


def test_error_message_names_the_variable_without_revealing_a_value(monkeypatch):
    monkeypatch.delenv(VARIABLE_NAME, raising=False)

    with pytest.raises(MissingConfigError) as raised:
        get_required_env(VARIABLE_NAME)

    assert VARIABLE_NAME in str(raised.value)
    assert SECRET_VALUE not in str(raised.value)


def test_reading_a_value_registers_it_for_redaction(monkeypatch):
    monkeypatch.setenv(VARIABLE_NAME, SECRET_VALUE)
    get_required_env(VARIABLE_NAME)

    redacted_text = redact(f"driver reported {SECRET_VALUE} in its error")

    assert SECRET_VALUE not in redacted_text
    assert REDACTION_PLACEHOLDER in redacted_text


def test_variable_names_match_the_documented_environment():
    assert MONGODB_URI_VARIABLE == "MONGODB_URI"
    assert GEMINI_API_KEY_VARIABLE == "GEMINI_API_KEY"
