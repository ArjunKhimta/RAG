"""`POST /ask`: answer one question about an indexed repository version.

Questions take turns (see `question_turns`): one is answered at a time, at most two wait, and a
waiting question gives up after 5 seconds. Searches do not take turns with questions. When
Gemini's per-minute limit would make a question wait more than 5 seconds for a slot, the shared
error replies refuse it at once. Together these bound a question's time in the service: at most
5 s for its turn, about 1 s of checks and search, and at most 80 s answering (the 75 s answer
budget plus one capped wait on a retry), 86 s in all, under the Node API's 90 s limit, so the
service never keeps working on an answer the caller has stopped waiting for.

Besides the shared error replies in `errors`, three failures belong to answering:
- 400: the question is too long for the reranker (only when it is used)
- 502: the answer model's reply failed the citation check; the reply itself is never shown
- 503 with `Retry-After: 10`: the service is busy with other questions (the queue is full, or the
  turn did not come in time)
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, request

from retrieval.answer_generation import AnswerRejectedError
from retrieval.asking import NoSourcesFoundError, QuestionAsker
from retrieval.config import SERVICE_BUSY_RETRY_SECONDS
from retrieval.redaction import redact
from retrieval.reranking import QuestionTooLongError
from retrieval.service.ask_request import parse_ask_request
from retrieval.service.ask_response import ask_response, no_sources_response
from retrieval.service.errors import BAD_REQUEST_STATUS, UNAVAILABLE_STATUS, error_reply
from retrieval.service.question_turns import QuestionsBusyError, QuestionTurns

BAD_GATEWAY_STATUS = 502

REJECTED_ANSWER_MESSAGE = "The answer model's reply failed the citation check and was not shown"

QUESTIONS_BUSY_MESSAGE = "The service is busy answering other questions; try again in a few seconds"


def register_ask_route(app: Flask, asker: QuestionAsker, question_turns: QuestionTurns) -> None:
    def ask() -> Response:
        ask_request = parse_ask_request(request.get_json(silent=True))
        try:
            with question_turns.turn():
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

    def questions_busy(error: QuestionsBusyError):
        app.logger.warning("Question refused: %s", redact(str(error)))
        return error_reply(
            QUESTIONS_BUSY_MESSAGE, UNAVAILABLE_STATUS, retry_after=SERVICE_BUSY_RETRY_SECONDS
        )

    app.add_url_rule("/ask", "ask", ask, methods=["POST"])
    app.register_error_handler(QuestionTooLongError, question_too_long)
    app.register_error_handler(AnswerRejectedError, rejected_answer)
    app.register_error_handler(QuestionsBusyError, questions_busy)
