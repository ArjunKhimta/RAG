"""`POST /index` and `GET /index-jobs/<job_id>`: index a repository version in the background.

`POST /index` answers at once. A version already indexed gets 200 with its repository block, the
same as in `POST /ask`, including the license link. A started job, or the same version already
being indexed, gets 202 with the job ID and a `Location` header to poll. A different version
while a job runs gets 503 with `Retry-After: 60`, since one job runs at a time.

With `"refresh": true`, an indexed version is checked against GitHub first: still at the indexed
commit (or a commit ID, which never moves), it gets 200 `up_to_date` with its repository block and
no job; moved, it gets a job as above, which patches the stored index. A tag or branch that no
longer exists gets 409, and GitHub out of reach 503 with `Retry-After: 30`. A version never
indexed is simply indexed.

`GET /index-jobs/<job_id>` returns the job's status. An ID that is not 32 hexadecimal characters
is refused as unknown without a database lookup. Any caller holding the service token can read
any job; only the Node API holds it, IDs cannot be guessed, and Node decides who sees which job.

Neither route waits on the lock that `POST /ask` and `POST /search` share: starting a job is one
lookup and one insert, and the job runs on its own thread with its own rate limiter. Database
failures and malformed bodies use the shared error replies in `errors`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime

from flask import Flask, Response, jsonify, request

from retrieval.chunk_store import require_license_path
from retrieval.indexing_jobs import IndexingJobRunner, StartKind
from retrieval.redaction import redact_exception
from retrieval.repository_cloner import CloneFailedError, VersionNotFoundError
from retrieval.service.ask_response import repository_block
from retrieval.service.errors import CONFLICT_STATUS, UNAVAILABLE_STATUS, error_reply
from retrieval.service.index_request import parse_index_request
from retrieval.service.index_response import job_link, job_response, started_response

ACCEPTED_STATUS = 202

NOT_FOUND_STATUS = 404

BUSY_RETRY_SECONDS = 60

JOB_ID_PATTERN = re.compile(r"[0-9a-f]{32}")

BUSY_MESSAGE = "Another repository is being indexed; try again shortly"

GITHUB_UNREACHABLE_RETRY_SECONDS = 30

GITHUB_UNREACHABLE_MESSAGE = "GitHub could not be reached to check for changes; try again shortly"

UNKNOWN_JOB_MESSAGE = "No such indexing job"

logger = logging.getLogger(__name__)


def register_index_routes(
    app: Flask, runner: IndexingJobRunner, now: Callable[[], datetime]
) -> None:
    def start_index() -> Response | tuple:
        index_request = parse_index_request(request.get_json(silent=True))
        repository = index_request.repository
        version = index_request.version
        try:
            if index_request.refresh:
                outcome = runner.refresh(repository, version)
            else:
                outcome = runner.start(repository, version)
        except VersionNotFoundError:
            message = f"{version} is no longer a tag or branch of {repository} on GitHub"
            return error_reply(message, CONFLICT_STATUS)
        except CloneFailedError as error:
            logger.warning("Could not check GitHub for changes: %s", redact_exception(error))
            return error_reply(
                GITHUB_UNREACHABLE_MESSAGE,
                UNAVAILABLE_STATUS,
                retry_after=GITHUB_UNREACHABLE_RETRY_SECONDS,
            )
        if outcome.kind in (StartKind.ALREADY_INDEXED, StartKind.UP_TO_DATE):
            record = outcome.repository_record
            require_license_path(record, repository, version)
            return jsonify(
                status=outcome.kind.value,
                repository=repository_block(record, repository, version),
            )
        if outcome.kind == StartKind.BUSY:
            return error_reply(BUSY_MESSAGE, UNAVAILABLE_STATUS, retry_after=BUSY_RETRY_SECONDS)
        return (
            jsonify(started_response(outcome.job_id)),
            ACCEPTED_STATUS,
            {"Location": job_link(outcome.job_id)},
        )

    def show_job(job_id: str) -> Response | tuple:
        if JOB_ID_PATTERN.fullmatch(job_id) is None:
            return error_reply(UNKNOWN_JOB_MESSAGE, NOT_FOUND_STATUS)
        job = runner.find_job(job_id)
        if job is None:
            return error_reply(UNKNOWN_JOB_MESSAGE, NOT_FOUND_STATUS)
        return jsonify(job_response(job, now()))

    app.add_url_rule("/index", "start_index", start_index, methods=["POST"])
    app.add_url_rule("/index-jobs/<job_id>", "show_index_job", show_job, methods=["GET"])
