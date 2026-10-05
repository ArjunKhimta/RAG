"""Error replies shared by `POST /ask` and `POST /search`.

Each failure has its own status, so the caller can tell its own mistake from a passing problem:
- 400: the body broke a rule (the message names the field)
- 409: this version cannot be searched: not indexed, still building, or indexed with another model
- 503 with `Retry-After`: a Gemini daily quota is used up (wait until midnight Pacific), or Gemini
  or the database failed for now (wait 30 seconds)
The messages say "Gemini" rather than naming a model, since the failing request may be the
question's embedding or the answer. Every message passes through the redaction module, and
problems are logged redacted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from flask import Flask, Response, jsonify
from pymongo.errors import PyMongoError

from retrieval.gemini_errors import GeminiRequestError
from retrieval.redaction import redact, redact_exception
from retrieval.search_results import SearchRefusedError
from retrieval.service.request_fields import InvalidRequestError
from retrieval.service.retry_after import (
    TEMPORARY_FAILURE_RETRY_SECONDS,
    seconds_until_daily_quota_reset,
)

BAD_REQUEST_STATUS = 400

CONFLICT_STATUS = 409

UNAVAILABLE_STATUS = 503

DAILY_QUOTA_MESSAGE = "Gemini's daily quota is used up; try again after it resets"

GEMINI_UNAVAILABLE_MESSAGE = "Gemini is unavailable for now; try again shortly"

DATABASE_UNAVAILABLE_MESSAGE = "The database is unavailable for now; try again shortly"


def register_error_replies(app: Flask, now: Callable[[], datetime]) -> None:
    def invalid_request(error: InvalidRequestError):
        return error_reply(str(error), BAD_REQUEST_STATUS)

    def refused_search(error: SearchRefusedError):
        app.logger.warning("Search refused: %s", redact_exception(error))
        return error_reply(str(error), CONFLICT_STATUS)

    def gemini_failure(error: GeminiRequestError):
        app.logger.warning("Gemini request failed: %s", redact_exception(error))
        if error.is_daily_quota_exhausted:
            return error_reply(
                DAILY_QUOTA_MESSAGE,
                UNAVAILABLE_STATUS,
                retry_after=seconds_until_daily_quota_reset(now()),
            )
        return error_reply(
            GEMINI_UNAVAILABLE_MESSAGE,
            UNAVAILABLE_STATUS,
            retry_after=TEMPORARY_FAILURE_RETRY_SECONDS,
        )

    def database_failure(error: PyMongoError):
        app.logger.warning("Database request failed: %s", redact_exception(error))
        return error_reply(
            DATABASE_UNAVAILABLE_MESSAGE,
            UNAVAILABLE_STATUS,
            retry_after=TEMPORARY_FAILURE_RETRY_SECONDS,
        )

    app.register_error_handler(InvalidRequestError, invalid_request)
    app.register_error_handler(SearchRefusedError, refused_search)
    app.register_error_handler(GeminiRequestError, gemini_failure)
    app.register_error_handler(PyMongoError, database_failure)


def error_reply(
    message: str, status: int, retry_after: int | None = None
) -> tuple[Response, int, dict[str, str]]:
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return jsonify(error=redact(message)), status, headers
