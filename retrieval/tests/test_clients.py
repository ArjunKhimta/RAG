from __future__ import annotations

import pytest

from retrieval import clients
from retrieval.config import MissingConfigError

EXAMPLE_URI = "mongodb+srv://appuser:hunter2@cluster0.mongodb.net/codesearch"

EXAMPLE_API_KEY = "AIzaSyExampleKeyValueForTests"


class RecordingMongoClient:
    def __init__(self, connection_uri, **keyword_arguments):
        self.connection_uri = connection_uri
        self.keyword_arguments = keyword_arguments


class RecordingGeminiClient:
    def __init__(self, api_key, http_options=None):
        self.api_key = api_key
        self.http_options = http_options


@pytest.fixture
def recorded_gemini_module(monkeypatch):
    class GeminiModule:
        Client = RecordingGeminiClient

    monkeypatch.setattr(clients, "genai", GeminiModule)
    return GeminiModule


def test_mongo_client_receives_the_configured_uri(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", EXAMPLE_URI)
    monkeypatch.setattr(clients, "MongoClient", RecordingMongoClient)

    client = clients.build_mongo_client()

    assert client.connection_uri == EXAMPLE_URI


def test_mongo_client_fails_fast_instead_of_using_the_driver_default(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", EXAMPLE_URI)
    monkeypatch.setattr(clients, "MongoClient", RecordingMongoClient)

    client = clients.build_mongo_client()

    configured_timeout = client.keyword_arguments["serverSelectionTimeoutMS"]

    assert configured_timeout == clients.SERVER_SELECTION_TIMEOUT_MS
    assert clients.SERVER_SELECTION_TIMEOUT_MS == 20000


def test_mongo_client_raises_when_the_uri_is_missing(monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(clients, "MongoClient", RecordingMongoClient)

    with pytest.raises(MissingConfigError) as raised:
        clients.build_mongo_client()

    assert raised.value.variable_name == "MONGODB_URI"


def test_gemini_client_receives_the_configured_key(monkeypatch, recorded_gemini_module):
    monkeypatch.setenv("GEMINI_API_KEY", EXAMPLE_API_KEY)

    client = clients.build_gemini_client()

    assert client.api_key == EXAMPLE_API_KEY


def test_every_gemini_call_gives_up_after_thirty_seconds(monkeypatch, recorded_gemini_module):
    monkeypatch.setenv("GEMINI_API_KEY", EXAMPLE_API_KEY)

    client = clients.build_gemini_client()

    assert client.http_options.timeout == 30_000


def test_the_real_gemini_client_accepts_the_time_limit(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", EXAMPLE_API_KEY)

    client = clients.build_gemini_client()

    assert client._api_client._http_options.timeout == 30_000


def test_gemini_client_raises_when_the_key_is_missing(monkeypatch, recorded_gemini_module):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(MissingConfigError) as raised:
        clients.build_gemini_client()

    assert raised.value.variable_name == "GEMINI_API_KEY"
