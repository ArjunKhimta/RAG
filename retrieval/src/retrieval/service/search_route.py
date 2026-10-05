"""`POST /search`: find the code that matches a query, without writing an answer.

Search uses vector search, as answers do, and never asks the answer model, so it keeps working
after the answer model's daily quota is used up, and takes tens of milliseconds rather than
seconds. It shares the question asker's embedding cache, so it waits on the same lock as
`POST /ask`. No matching code is a normal reply with an empty list. Failures use the shared error
replies in `errors`.
"""

from __future__ import annotations

import threading

from flask import Flask, Response, jsonify, request

from retrieval.asking import QuestionAsker
from retrieval.service.search_request import parse_search_request
from retrieval.service.search_response import search_response


def register_search_route(app: Flask, asker: QuestionAsker, request_lock: threading.Lock) -> None:
    def search() -> Response:
        search_request = parse_search_request(request.get_json(silent=True))
        with request_lock:
            search_run = asker.search(
                search_request.query,
                search_request.repository,
                search_request.version,
                limit=search_request.limit,
                exclude_tests=search_request.exclude_tests,
            )
        return jsonify(
            search_response(search_run, search_request.repository, search_request.version)
        )

    app.add_url_rule("/search", "search", search, methods=["POST"])
