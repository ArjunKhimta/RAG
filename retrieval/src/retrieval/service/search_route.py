"""`POST /search`: find the code that matches a query, without writing an answer.

Search uses vector search, as answers do, and never asks the answer model, so it keeps working
after the answer model's daily quota is used up, and takes tens of milliseconds rather than
seconds. It never waits for a question's turn: the parts it shares with questions (the embedding
cache and its per-minute limiter) are safe to use from several threads, so a search is answered
while a question is being answered. No matching code is a normal reply with an empty list.
Failures use the shared error replies in `errors`.
"""

from __future__ import annotations

from flask import Flask, Response, jsonify, request

from retrieval.asking import QuestionAsker
from retrieval.service.search_request import parse_search_request
from retrieval.service.search_response import search_response


def register_search_route(app: Flask, asker: QuestionAsker) -> None:
    def search() -> Response:
        search_request = parse_search_request(request.get_json(silent=True))
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
