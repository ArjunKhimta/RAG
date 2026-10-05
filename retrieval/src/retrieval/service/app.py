"""The retrieval service: the search engine behind a private HTTP interface.

Only the Node API calls this service. Node owns everything about people: sign-in, who owns which
repository, and rate limits. This service does the retrieval work and trusts one caller, proven
by a shared secret token sent as `Authorization: Bearer <token>` on every request except the
health check. The token comes from `RETRIEVAL_SERVICE_TOKEN`; the service refuses to start without
it, or with one shorter than 32 characters, since a short token can be guessed.

The token check runs before routing, so a caller without the token gets 401 for every path,
including ones that do not exist, and learns nothing about which routes the service has. Tokens
are compared in constant time (`hmac.compare_digest`), so the time a wrong guess takes does not
reveal how much of it was right.

Every error is JSON, `{"error": "..."}`, with the HTTP status. An unexpected exception becomes a
generic 500: its details, which could hold a database address or an API key, are written to the
log only after passing through the redaction module, and never sent to the caller. Request bodies
are capped at 16 KB, plenty for a question, so a huge body is refused before it is read.

The question asker is built once, at start-up, so a missing setting or unreachable database stops
the service at launch rather than on the first question. Tests pass their own asker instead.
"""

from __future__ import annotations

import hmac
import traceback
from collections.abc import Callable
from datetime import UTC, datetime

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from retrieval.asking import QuestionAsker, build_question_asker
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    MINIMUM_SERVICE_TOKEN_LENGTH,
    MONGODB_DATABASE,
    SERVICE_MAX_REQUEST_BYTES,
    SERVICE_TOKEN_VARIABLE,
    get_required_env,
    load_environment,
)
from retrieval.redaction import redact, register_secret
from retrieval.service.ask_route import register_ask_route

BEARER_PREFIX = "Bearer "

HEALTH_ENDPOINT = "health"

UNAUTHORIZED_MESSAGE = "A valid service token is required"

INTERNAL_ERROR_MESSAGE = "The service hit an unexpected error"

INTERNAL_ERROR_STATUS = 500

UNAUTHORIZED_STATUS = 401


class WeakServiceTokenError(ValueError):
    """Raised when the service token is too short to be safe; never includes the token."""


def create_app(
    service_token: str | None = None,
    asker: QuestionAsker | None = None,
    now: Callable[[], datetime] | None = None,
) -> Flask:
    """Build the service. Tests pass a token, an asker, and a clock; otherwise the token comes
    from the environment, the asker is wired to Atlas and Gemini, and the clock is real."""
    token = service_token if service_token is not None else _token_from_environment()
    _require_strong_token(token)
    register_secret(token)
    question_asker = asker if asker is not None else _build_asker()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = SERVICE_MAX_REQUEST_BYTES
    app.json.sort_keys = False
    app.add_url_rule("/health", HEALTH_ENDPOINT, _health, methods=["GET"])
    register_ask_route(app, question_asker, now or _utc_now)
    app.before_request(_token_check(token))
    app.register_error_handler(HTTPException, _http_error)
    app.register_error_handler(Exception, _unexpected_error(app))
    return app


def _token_from_environment() -> str:
    load_environment()
    return get_required_env(SERVICE_TOKEN_VARIABLE)


def _build_asker() -> QuestionAsker:
    load_environment()
    database = build_mongo_client()[MONGODB_DATABASE]
    return build_question_asker(database, build_gemini_client())


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_strong_token(token: str) -> None:
    if len(token) < MINIMUM_SERVICE_TOKEN_LENGTH:
        raise WeakServiceTokenError(
            f"{SERVICE_TOKEN_VARIABLE} must be at least {MINIMUM_SERVICE_TOKEN_LENGTH} characters"
        )


def _health() -> Response:
    return jsonify(status="ok")


def _token_check(expected_token: str):
    expected_bytes = expected_token.encode("utf-8")

    def check_token() -> tuple[Response, int, dict[str, str]] | None:
        if request.endpoint == HEALTH_ENDPOINT:
            return None
        header = request.headers.get("Authorization", "")
        if not header.startswith(BEARER_PREFIX):
            return _unauthorized()
        given_bytes = header.removeprefix(BEARER_PREFIX).encode("utf-8")
        if not hmac.compare_digest(given_bytes, expected_bytes):
            return _unauthorized()
        return None

    return check_token


def _unauthorized() -> tuple[Response, int, dict[str, str]]:
    return (
        jsonify(error=UNAUTHORIZED_MESSAGE),
        UNAUTHORIZED_STATUS,
        {"WWW-Authenticate": "Bearer"},
    )


def _http_error(error: HTTPException) -> tuple[Response, int]:
    status = error.code or INTERNAL_ERROR_STATUS
    return jsonify(error=redact(error.description or error.name)), status


def _unexpected_error(app: Flask):
    def handle(error: Exception) -> tuple[Response, int]:
        details = "".join(traceback.format_exception(error))
        app.logger.error("Unexpected error: %s", redact(details))
        return jsonify(error=INTERNAL_ERROR_MESSAGE), INTERNAL_ERROR_STATUS

    return handle
