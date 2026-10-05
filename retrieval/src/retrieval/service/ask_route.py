"""`POST /ask`: answer one question about an indexed repository version.

Requests are handled one at a time per process, behind a lock shared with `POST /search`. The
question asker's rate limiter and the embedding cache's counters are not safe to share between
threads, and the lock costs little in practice: the free answer model allows 15 answers a minute,
while one process answers about 30, so Gemini, not the lock, sets the pace. When the per-minute
limit is reached, the request waits inside the rate limiter until a slot frees, at most about a
minute.

Besides the shared error replies in `errors`, two failures belong to answering:
- 400: the question is too long for the reranker (only when it is used)
- 502: the answer model's reply failed the citation check; the reply itself is never shown
"""

from __future__ import annotations

import threading

from flask import Flask, Response, jsonify, request

from retrieval.answer_generation import AnswerRejectedError
from retrieval.asking import NoSourcesFoundError, QuestionAsker
from retrieval.redaction import redact
from retrieval.reranking import QuestionTooLongError
from retrieval.service.ask_request import parse_ask_request
from retrieval.service.ask_response import ask_response, no_sources_response
from retrieval.service.errors import BAD_REQUEST_STATUS, error_reply

BAD_GATEWAY_STATUS = 502

REJECTED_ANSWER_MESSAGE = "The answer model's reply failed the citation check and was not shown"


def register_ask_route(app: Flask, asker: QuestionAsker, request_lock: threading.Lock) -> None:
    def ask() -> Response:
        ask_request = parse_ask_request(request.get_json(silent=True))
        try:
            with request_lock:
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

    def question_too_long(error: QuestionTooLongError):
        return error_reply(str(error), BAD_REQUEST_STATUS)

    def rejected_answer(error: AnswerRejectedError):
        app.logger.warning("Answer rejected: %s", redact("; ".join(error.problems)))
        return error_reply(REJECTED_ANSWER_MESSAGE, BAD_GATEWAY_STATUS)

    app.add_url_rule("/ask", "ask", ask, methods=["POST"])
    app.register_error_handler(QuestionTooLongError, question_too_long)
    app.register_error_handler(AnswerRejectedError, rejected_answer)
