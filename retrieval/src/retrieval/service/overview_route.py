"""`POST /overview`: a repository version's files and how they call each other, from the stored
call graph. It asks neither Gemini nor the reranker and shares no state with questions, so it does
not wait on the question lock. A version that is not indexed, was indexed before license paths or
the call graph, is a 409 through the shared error replies."""

from __future__ import annotations

from flask import Flask, Response, jsonify, request

from retrieval.repository_overview import RepositoryOverviewBuilder
from retrieval.service.overview_request import parse_overview_request
from retrieval.service.overview_response import overview_response


def register_overview_route(app: Flask, builder: RepositoryOverviewBuilder) -> None:
    def overview() -> Response:
        overview_request = parse_overview_request(request.get_json(silent=True))
        run = builder.overview(overview_request.repository, overview_request.version)
        return jsonify(
            overview_response(run, overview_request.repository, overview_request.version)
        )

    app.add_url_rule("/overview", "overview", overview, methods=["POST"])
