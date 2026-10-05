from __future__ import annotations

import logging

import pytest

from retrieval.config import MissingConfigError
from retrieval.redaction import REDACTION_PLACEHOLDER, clear_registered_secrets
from retrieval.service import app as service_app
from retrieval.service.app import WeakServiceTokenError, create_app

SERVICE_TOKEN = "test-service-token-0123456789abcdef"

DATABASE_PASSWORD = "database-password-in-an-error"


@pytest.fixture(autouse=True)
def _forget_registered_secrets():
    yield
    clear_registered_secrets()


class UnusedAsker:
    def ask(self, *arguments, **options):
        raise AssertionError("these tests never ask a question")


@pytest.fixture
def app():
    return create_app(service_token=SERVICE_TOKEN, asker=UnusedAsker())


@pytest.fixture
def client(app):
    return app.test_client()


def _authorized(token: str = SERVICE_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_health_check_needs_no_token(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_every_other_path_needs_the_token_even_when_it_does_not_exist(client):
    response = client.get("/no-such-route")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.get_json() == {"error": "A valid service token is required"}


@pytest.mark.parametrize(
    "header",
    [
        f"Bearer {SERVICE_TOKEN}x",
        f"Bearer {SERVICE_TOKEN[:-1]}",
        "Bearer ",
        f"Token {SERVICE_TOKEN}",
        SERVICE_TOKEN,
        f"bearer {SERVICE_TOKEN}",
    ],
)
def test_a_wrong_or_malformed_token_is_refused(client, header):
    response = client.get("/no-such-route", headers={"Authorization": header})

    assert response.status_code == 401


def test_the_right_token_reaches_routing(client):
    response = client.get("/no-such-route", headers=_authorized())

    assert response.status_code == 404
    assert response.is_json
    assert "error" in response.get_json()


def test_a_wrong_method_is_a_json_error(client):
    response = client.post("/health", headers=_authorized())

    assert response.status_code == 405
    assert response.is_json


def test_an_unexpected_error_hides_its_details_and_logs_them_redacted(app, caplog):
    def failing_route():
        raise RuntimeError(f"mongodb+srv://user:{DATABASE_PASSWORD}@cluster/db is unreachable")

    app.add_url_rule("/fail", "fail", failing_route)
    client = app.test_client()

    with caplog.at_level(logging.ERROR):
        response = client.get("/fail", headers=_authorized())

    assert response.status_code == 500
    assert response.get_json() == {"error": "The service hit an unexpected error"}
    assert DATABASE_PASSWORD not in response.get_data(as_text=True)
    assert "RuntimeError" in caplog.text
    assert REDACTION_PLACEHOLDER in caplog.text
    assert DATABASE_PASSWORD not in caplog.text


def test_the_token_never_appears_in_logged_errors(app, caplog):
    def leaking_route():
        raise RuntimeError(f"header was {SERVICE_TOKEN}")

    app.add_url_rule("/leak", "leak", leaking_route)

    with caplog.at_level(logging.ERROR):
        app.test_client().get("/leak", headers=_authorized())

    assert SERVICE_TOKEN not in caplog.text


def test_request_bodies_are_capped_and_debug_is_off(app):
    assert app.config["MAX_CONTENT_LENGTH"] == 16 * 1024
    assert not app.debug


def test_a_short_token_stops_start_up_without_revealing_it():
    short_token = "short-token-123"

    with pytest.raises(WeakServiceTokenError) as raised:
        create_app(service_token=short_token, asker=UnusedAsker())

    assert short_token not in str(raised.value)
    assert "RETRIEVAL_SERVICE_TOKEN" in str(raised.value)


def test_a_missing_token_stops_start_up(monkeypatch):
    monkeypatch.setattr(service_app, "load_environment", lambda: None)
    monkeypatch.delenv("RETRIEVAL_SERVICE_TOKEN", raising=False)

    with pytest.raises(MissingConfigError):
        create_app()


def test_the_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.setattr(service_app, "load_environment", lambda: None)
    monkeypatch.setenv("RETRIEVAL_SERVICE_TOKEN", SERVICE_TOKEN)

    client = create_app(asker=UnusedAsker()).test_client()

    assert client.get("/no-such-route", headers=_authorized()).status_code == 404
