"""The JSON replies of `POST /index` and `GET /index-jobs/<job_id>`.

A started job's reply gives its ID and where to poll. A job's status reply copies its record:
status, stage, embedding progress, the error code and message when it failed, the report and
stage timings when it succeeded. Times are UTC in ISO 8601 with a `Z`; MongoDB returns them
without a time zone, so they are marked UTC here, in one place. A job waiting for the daily
quota gives both `resume_after`, the moment the budget resets, and `retry_after_seconds`, how
long that is from now, so the caller need not do date arithmetic.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from retrieval.indexing_jobs import JobStatus

INDEX_JOBS_PATH = "/index-jobs/{job_id}"


def job_link(job_id: str) -> str:
    return INDEX_JOBS_PATH.format(job_id=job_id)


def started_response(job_id: str) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": JobStatus.RUNNING.value,
        "status_link": job_link(job_id),
    }


def job_response(job: dict[str, Any], now: datetime) -> dict[str, Any]:
    resume_after = _as_utc(job["resume_after"])
    return {
        "job_id": job["_id"],
        "repository": job["repository"],
        "version": job["version"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": {"embedded": job["embedded_count"], "to_embed": job["to_embed_count"]},
        "error": _error(job),
        "resume_after": _iso(resume_after),
        "retry_after_seconds": _seconds_until(resume_after, now),
        "result": job["result"],
        "timings_ms": job["timings_ms"],
        "created_at": _iso(_as_utc(job["created_at"])),
        "finished_at": _iso(_as_utc(job["finished_at"])),
    }


def _error(job: dict[str, Any]) -> dict[str, Any] | None:
    if job["error_message"] is None:
        return None
    return {"code": job["error_code"], "message": job["error_message"]}


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _seconds_until(moment: datetime | None, now: datetime) -> int | None:
    if moment is None:
        return None
    return max(0, math.ceil((moment - now.astimezone(UTC)).total_seconds()))
