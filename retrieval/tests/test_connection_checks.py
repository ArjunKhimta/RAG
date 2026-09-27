from __future__ import annotations

import pytest

from retrieval import connection_checks
from retrieval.config import GEMINI_EMBEDDING_MODEL, MissingConfigError
from retrieval.redaction import register_secret

EXAMPLE_URI = "mongodb+srv://appuser:hunter2@cluster0.mongodb.net/codesearch"

EXAMPLE_API_KEY = "AIzaSyExampleKeyValueForTests"


class FakeAdminDatabase:
    def __init__(self):
        self.commands_run = []

    def command(self, command_name):
        self.commands_run.append(command_name)
        return {"ok": 1}


class FakeMongoClient:
    def __init__(self, server_version="7.0.14"):
        self.admin = FakeAdminDatabase()
        self.server_version = server_version
        self.closed = False

    def server_info(self):
        return {"version": self.server_version}

    def close(self):
        self.closed = True


class FakeModel:
    def __init__(self, name):
        self.name = name


class FakeModels:
    def __init__(self, model_names, embed_error=None):
        self.model_names = model_names
        self.embed_error = embed_error
        self.embed_calls = []

    def list(self):
        return [FakeModel(name) for name in self.model_names]

    def embed_content(self, model, contents):
        self.embed_calls.append((model, contents))
        if self.embed_error is not None:
            raise self.embed_error
        return {"embeddings": [[0.0, 1.0]]}


class FakeGeminiClient:
    def __init__(self, model_names, embed_error=None):
        self.models = FakeModels(model_names, embed_error)


def test_mongodb_check_pings_and_reports_the_server_version(monkeypatch):
    fake_client = FakeMongoClient()
    monkeypatch.setattr(connection_checks, "build_mongo_client", lambda: fake_client)

    result = connection_checks.check_mongodb()

    assert result.passed
    assert "7.0.14" in result.detail
    assert fake_client.admin.commands_run == ["ping"]
    assert fake_client.closed


def test_mongodb_check_survives_a_cluster_that_withholds_build_info(monkeypatch):
    fake_client = FakeMongoClient()
    monkeypatch.setattr(fake_client, "server_info", _raise_permission_error)
    monkeypatch.setattr(connection_checks, "build_mongo_client", lambda: fake_client)

    result = connection_checks.check_mongodb()

    assert result.passed
    assert "version unavailable" in result.detail


def test_mongodb_check_redacts_credentials_leaked_by_a_driver_error(monkeypatch):
    register_secret(EXAMPLE_URI)

    def raise_driver_error():
        raise ConnectionError(f"could not reach {EXAMPLE_URI} within 5000 ms")

    monkeypatch.setattr(connection_checks, "build_mongo_client", raise_driver_error)

    result = connection_checks.check_mongodb()

    assert not result.passed
    assert "hunter2" not in result.detail
    assert EXAMPLE_URI not in result.detail


def test_mongodb_check_reports_a_missing_variable_by_name(monkeypatch):
    def raise_missing_config():
        raise MissingConfigError("MONGODB_URI")

    monkeypatch.setattr(connection_checks, "build_mongo_client", raise_missing_config)

    result = connection_checks.check_mongodb()

    assert not result.passed
    assert "MONGODB_URI" in result.detail


def test_gemini_check_passes_when_the_embedding_model_is_listed(monkeypatch):
    fake_client = FakeGeminiClient([f"models/{GEMINI_EMBEDDING_MODEL}", "models/gemini-2.5-flash"])
    monkeypatch.setattr(connection_checks, "build_gemini_client", lambda: fake_client)

    result = connection_checks.check_gemini()

    assert result.passed
    assert GEMINI_EMBEDDING_MODEL in result.detail
    assert fake_client.models.embed_calls == []


def test_gemini_check_falls_back_to_a_test_embedding_when_the_model_is_not_listed(monkeypatch):
    fake_client = FakeGeminiClient(["models/gemini-2.5-flash"])
    monkeypatch.setattr(connection_checks, "build_gemini_client", lambda: fake_client)

    result = connection_checks.check_gemini()

    assert result.passed
    assert fake_client.models.embed_calls == [(GEMINI_EMBEDDING_MODEL, "ping")]


def test_gemini_check_redacts_an_api_key_echoed_in_an_error(monkeypatch):
    register_secret(EXAMPLE_API_KEY)
    fake_client = FakeGeminiClient(
        ["models/gemini-2.5-flash"],
        embed_error=RuntimeError(f"401 from https://example.test/v1?key={EXAMPLE_API_KEY}"),
    )
    monkeypatch.setattr(connection_checks, "build_gemini_client", lambda: fake_client)

    result = connection_checks.check_gemini()

    assert not result.passed
    assert EXAMPLE_API_KEY not in result.detail
    assert "RuntimeError" in result.detail


def test_model_listing_is_capped_so_a_long_pager_cannot_run_away(monkeypatch):
    excess_count = connection_checks.MAXIMUM_MODELS_EXAMINED + 50
    fake_client = FakeGeminiClient([f"models/model-{index}" for index in range(excess_count)])
    monkeypatch.setattr(connection_checks, "build_gemini_client", lambda: fake_client)

    examined_names = list(connection_checks._iterate_model_names(fake_client))

    assert len(examined_names) == connection_checks.MAXIMUM_MODELS_EXAMINED


def test_run_all_checks_covers_both_services(monkeypatch):
    monkeypatch.setattr(connection_checks, "build_mongo_client", FakeMongoClient)
    monkeypatch.setattr(
        connection_checks,
        "build_gemini_client",
        lambda: FakeGeminiClient([f"models/{GEMINI_EMBEDDING_MODEL}"]),
    )

    results = connection_checks.run_all_checks()

    assert [result.service_name for result in results] == ["MongoDB", "Gemini"]
    assert all(result.passed for result in results)


def _raise_permission_error():
    raise PermissionError("not authorized on admin to execute command buildinfo")


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Guarantee these tests can never construct a real client."""
    monkeypatch.setattr(
        connection_checks,
        "build_mongo_client",
        lambda: pytest.fail("a test reached the real MongoDB client builder"),
    )
    monkeypatch.setattr(
        connection_checks,
        "build_gemini_client",
        lambda: pytest.fail("a test reached the real Gemini client builder"),
    )
