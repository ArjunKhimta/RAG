"""`POST /ask`: answer one question about an indexed repository version.

Questions are answered one at a time per process, behind a lock. The question asker's rate
limiter and counters are not safe to share between threads, and the lock costs nothing in
practice: the free answer model allows 15 answers a minute, while one process answers about 30,
so Gemini, not the lock, sets the pace. When the per-minute limit is reached, the request waits
inside the rate limiter until a slot frees, at most about a minute.

Each failure has its own status, so the caller can tell its own mistake from a passing problem:
- 400: the body broke a rule (the message names the field)
- 409: this version cannot be searched: not indexed, still building, or indexed with another model
- 502: the answer model's reply failed the citation check; the reply itself is never shown
- 503 with `Retry-After`: Gemini's daily quota is used up (wait until midnight Pacific), or Gemini
  or the database failed for now (wait 30 seconds)
Every message passes through the redaction module, and problems are logged redacted.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from flask import Flask, Response, jsonify, request
from pymongo.errors import PyMongoError

from retrieval.answer_generation import AnswerRejectedError
from retrieval.asking import NoSourcesFoundError, QuestionAsker
from retrieval.gemini_errors import GeminiRequestError
from retrieval.redaction import redact, redact_exception
from retrieval.reranking import QuestionTooLongError
from retrieval.search_results import SearchRefusedError
from retrieval.service.ask_request import InvalidAskRequestError, parse_ask_request
from retrieval.service.ask_response import ask_response, no_sources_response
from retrieval.service.retry_after import (
    TEMPORARY_FAILURE_RETRY_SECONDS,
    seconds_until_daily_quota_reset,
)

BAD_REQUEST_STATUS = 400

CONFLICT_STATUS = 409

BAD_GATEWAY_STATUS = 502

UNAVAILABLE_STATUS = 503

REJECTED_ANSWER_MESSAGE = "The answer model's reply failed the citation check and was not shown"

DAILY_QUOTA_MESSAGE = "The answer model's daily quota is used up; try again after it resets"

GEMINI_UNAVAILABLE_MESSAGE = "The answer model is unavailable for now; try again shortly"

DATABASE_UNAVAILABLE_MESSAGE = "The database is unavailable for now; try again shortly"


def register_ask_route(app: Flask, asker: QuestionAsker, now: Callable[[], datetime]) -> None:
    ask_lock = threading.Lock()

    def ask() -> Response:
        ask_request = parse_ask_request(request.get_json(silent=True))
        try:
            with ask_lock:
                ask_run = asker.ask(
                    ask_request.question,
                    ask_request.repository,
                    ask_request.version,
                    exclude_tests=ask_request.exclude_tests,
                )
        except NoSourcesFoundError as error:
            return jsonify(
                no_sources_response(
                    error.repository_record, ask_request.repository, ask_request.version
                )
            )
        return jsonify(ask_response(ask_run, ask_request.repository, ask_request.version))

    def invalid_request(error: InvalidAskRequestError | QuestionTooLongError):
        return _error(str(error), BAD_REQUEST_STATUS)

    def refused_search(error: SearchRefusedError):
        app.logger.warning("Search refused: %s", redact_exception(error))
        return _error(str(error), CONFLICT_STATUS)

    def rejected_answer(error: AnswerRejectedError):
        app.logger.warning("Answer rejected: %s", redact("; ".join(error.problems)))
        return _error(REJECTED_ANSWER_MESSAGE, BAD_GATEWAY_STATUS)

    def gemini_failure(error: GeminiRequestError):
        app.logger.warning("Gemini request failed: %s", redact_exception(error))
        if error.is_daily_quota_exhausted:
            return _error(
                DAILY_QUOTA_MESSAGE,
                UNAVAILABLE_STATUS,
                retry_after=seconds_until_daily_quota_reset(now()),
            )
        return _error(
            GEMINI_UNAVAILABLE_MESSAGE,
            UNAVAILABLE_STATUS,
            retry_after=TEMPORARY_FAILURE_RETRY_SECONDS,
        )

    def database_failure(error: PyMongoError):
        app.logger.warning("Database request failed: %s", redact_exception(error))
        return _error(
            DATABASE_UNAVAILABLE_MESSAGE,
            UNAVAILABLE_STATUS,
            retry_after=TEMPORARY_FAILURE_RETRY_SECONDS,
        )

    app.add_url_rule("/ask", "ask", ask, methods=["POST"])
    app.register_error_handler(InvalidAskRequestError, invalid_request)
    app.register_error_handler(QuestionTooLongError, invalid_request)
    app.register_error_handler(SearchRefusedError, refused_search)
    app.register_error_handler(AnswerRejectedError, rejected_answer)
    app.register_error_handler(GeminiRequestError, gemini_failure)
    app.register_error_handler(PyMongoError, database_failure)


def _error(
    message: str, status: int, retry_after: int | None = None
) -> tuple[Response, int, dict[str, str]]:
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return jsonify(error=redact(message)), status, headers
