"""Index repository versions in the background, one job at a time, keeping each job's status.

Indexing takes minutes, too long to hold an HTTP request open, so a request starts a job and gets
its ID at once, and the caller polls the job's record. Starting a job checks, in order: a version
already indexed needs no job; the same version already running returns that job; a different job
running means busy; otherwise a job record is saved and the work starts on a background thread.

Only one job runs at a time per process, tracked in memory: every job shares the indexing lane's
per-minute limit, and one job at a time keeps memory predictable on a small server. This assumes
the service runs as a single process; with more, each would run its own job.

The job record carries the stage, the embedding progress ("300 of 1,009"), and the outcome:
- `succeeded`, with the indexing report and stage timings
- `waiting_for_quota`, with `resume_after`, the next midnight Pacific: the day's indexing budget
  ran out, everything embedded so far is stored, and starting the same version again resumes it
- `failed`, with an error code: `not_found`, `not_public`, `license_not_allowed`, `too_large`,
  `too_many_chunks`, `no_python_code`, `clone_failed` (often a version that does not exist), or
  `unexpected`, whose details go only to the log, redacted
- `interrupted`: the process stopped mid-job; at start-up every job still `running` is marked so

A job deletes the checkout it cloned, whether it succeeds or fails, since answers and snippets come
from Atlas; a checkout that already existed, such as a developer's clone, is never deleted. Job
IDs are 32 random hexadecimal characters, so one cannot be guessed. Records are deleted 30 days
after they are created. The service never knows who asked; the caller links jobs to people.
"""

from __future__ import annotations

import logging
import secrets
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from google import genai
from pymongo import ASCENDING
from pymongo.database import Database
from pymongo.errors import PyMongoError

from retrieval.chunk_store import MongoChunkStore
from retrieval.config import INDEXING_JOB_TTL_SECONDS, REPOSITORIES_DIRECTORY
from retrieval.embedders import DocumentEmbedder, EmbeddingRequestError
from retrieval.embedding_budget import build_document_embedder, build_indexing_rate_limiter
from retrieval.github_urls import RepositoryReference
from retrieval.quota_clock import seconds_until_daily_quota_reset
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact, redact_exception
from retrieval.repository_cloner import (
    ClonedRepository,
    CloneFailedError,
    RepositoryNotFoundError,
    RepositoryNotPublicError,
    RepositoryTooLargeError,
    UnacceptableLicenseError,
    UnsafeDeletionError,
    clone_repository,
    remove_directory_inside,
)
from retrieval.repository_indexing import (
    IndexingStage,
    NoPythonCodeError,
    RepositoryIndexingRun,
    TooManyChunksError,
    index_repository_version,
)

INDEXING_JOBS_COLLECTION = "indexing_jobs"

JOB_ID_BYTES = 16

CLONE_FAILED_MESSAGE = (
    "The version could not be downloaded; check that the tag, branch, or commit exists"
)

UNEXPECTED_MESSAGE = "Indexing hit an unexpected error"

INTERRUPTED_MESSAGE = "The service stopped during this job; start it again to resume"

QUOTA_MESSAGE = (
    "Today's indexing budget is used up; everything embedded so far is stored, and starting "
    "this version again after resume_after continues from there"
)

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    WAITING_FOR_QUOTA = "waiting_for_quota"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    NOT_PUBLIC = "not_public"
    LICENSE_NOT_ALLOWED = "license_not_allowed"
    TOO_LARGE = "too_large"
    TOO_MANY_CHUNKS = "too_many_chunks"
    NO_PYTHON_CODE = "no_python_code"
    CLONE_FAILED = "clone_failed"
    UNEXPECTED = "unexpected"


REFUSAL_CODES: tuple[tuple[type[Exception], JobErrorCode], ...] = (
    (RepositoryNotFoundError, JobErrorCode.NOT_FOUND),
    (RepositoryNotPublicError, JobErrorCode.NOT_PUBLIC),
    (UnacceptableLicenseError, JobErrorCode.LICENSE_NOT_ALLOWED),
    (RepositoryTooLargeError, JobErrorCode.TOO_LARGE),
    (TooManyChunksError, JobErrorCode.TOO_MANY_CHUNKS),
    (NoPythonCodeError, JobErrorCode.NO_PYTHON_CODE),
)


class StartKind(StrEnum):
    ALREADY_INDEXED = "already_indexed"
    ALREADY_RUNNING = "already_running"
    STARTED = "started"
    BUSY = "busy"


@dataclass(frozen=True)
class StartOutcome:
    kind: StartKind
    job_id: str | None = None
    repository_record: dict[str, Any] | None = None


class JobStore(Protocol):
    def create(self, job_id: str, repository: str, version: str, now: datetime) -> None: ...

    def update(self, job_id: str, fields: dict[str, Any], now: datetime) -> None: ...

    def find(self, job_id: str) -> dict[str, Any] | None: ...

    def mark_interrupted(self, now: datetime) -> int: ...


class RepositoryRecords(Protocol):
    def find_repository_record(self, repository: str, version: str) -> dict[str, Any] | None: ...


class MongoJobStore:
    def __init__(self, database: Database) -> None:
        self._jobs = database[INDEXING_JOBS_COLLECTION]

    def ensure_indexes(self) -> None:
        self._jobs.create_index(
            [("created_at", ASCENDING)], expireAfterSeconds=INDEXING_JOB_TTL_SECONDS
        )

    def create(self, job_id: str, repository: str, version: str, now: datetime) -> None:
        self._jobs.insert_one(new_job_document(job_id, repository, version, now))

    def update(self, job_id: str, fields: dict[str, Any], now: datetime) -> None:
        self._jobs.update_one({"_id": job_id}, {"$set": {**fields, "updated_at": now}})

    def find(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.find_one({"_id": job_id})

    def mark_interrupted(self, now: datetime) -> int:
        result = self._jobs.update_many(
            {"status": JobStatus.RUNNING.value},
            {"$set": interrupted_fields(now)},
        )
        return result.modified_count


def new_job_document(job_id: str, repository: str, version: str, now: datetime) -> dict[str, Any]:
    return {
        "_id": job_id,
        "repository": repository,
        "version": version,
        "status": JobStatus.RUNNING.value,
        "stage": None,
        "embedded_count": 0,
        "to_embed_count": None,
        "error_code": None,
        "error_message": None,
        "resume_after": None,
        "result": None,
        "timings_ms": {},
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }


def interrupted_fields(now: datetime) -> dict[str, Any]:
    return {
        "status": JobStatus.INTERRUPTED.value,
        "error_message": INTERRUPTED_MESSAGE,
        "updated_at": now,
        "finished_at": now,
    }


IndexVersion = Callable[..., RepositoryIndexingRun]

StartThread = Callable[[Callable[[], None]], None]


class IndexingJobRunner:
    def __init__(
        self,
        database: Database,
        job_store: JobStore,
        repository_records: RepositoryRecords,
        embedder: DocumentEmbedder,
        rate_limiter: RateLimiter,
        index_version: IndexVersion = index_repository_version,
        clone: Callable[[RepositoryReference, str], ClonedRepository] = clone_repository,
        start_thread: StartThread | None = None,
        repositories_directory: Path = REPOSITORIES_DIRECTORY,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_job_id: Callable[[], str] = lambda: secrets.token_hex(JOB_ID_BYTES),
    ) -> None:
        self._database = database
        self._job_store = job_store
        self._repository_records = repository_records
        self._embedder = embedder
        self._rate_limiter = rate_limiter
        self._index_version = index_version
        self._clone = clone
        self._start_thread = start_thread or start_daemon_thread
        self._repositories_directory = repositories_directory
        self._clock = clock
        self._new_job_id = new_job_id
        self._lock = threading.Lock()
        self._running: tuple[str, str, str] | None = None

    def start(self, repository: str, version: str) -> StartOutcome:
        """Start indexing `repository` (owner/repo) at `version`, unless not needed or busy."""
        with self._lock:
            record = self._repository_records.find_repository_record(repository, version)
            if record is not None:
                return StartOutcome(StartKind.ALREADY_INDEXED, repository_record=record)
            if self._running is not None:
                running_job_id, running_repository, running_version = self._running
                if (running_repository, running_version) == (repository, version):
                    return StartOutcome(StartKind.ALREADY_RUNNING, job_id=running_job_id)
                return StartOutcome(StartKind.BUSY)
            job_id = self._new_job_id()
            self._job_store.create(job_id, repository, version, self._clock())
            self._running = (job_id, repository, version)
        self._start_thread(lambda: self._run(job_id, repository, version))
        return StartOutcome(StartKind.STARTED, job_id=job_id)

    def find_job(self, job_id: str) -> dict[str, Any] | None:
        return self._job_store.find(job_id)

    def _run(self, job_id: str, repository: str, version: str) -> None:
        owner, name = repository.split("/")
        cloned_paths: list[Path] = []

        def clone_and_remember(reference: RepositoryReference, clone_version: str):
            cloned = self._clone(reference, clone_version)
            if not cloned.was_reused:
                cloned_paths.append(cloned.path)
            return cloned

        try:
            indexing_run = self._index_version(
                RepositoryReference(owner=owner, name=name),
                version,
                self._database,
                self._embedder,
                self._rate_limiter,
                clone=clone_and_remember,
                on_stage=lambda stage: self._record_stage(job_id, stage),
                on_progress=lambda embedded, to_embed: self._record_progress(
                    job_id, embedded, to_embed
                ),
            )
            self._finish(job_id, succeeded_fields(indexing_run))
        except Exception as error:
            self._finish(job_id, self._failure_fields(error))
        finally:
            self._remove_checkouts(cloned_paths)
            with self._lock:
                self._running = None

    def _record_stage(self, job_id: str, stage: IndexingStage) -> None:
        self._job_store.update(job_id, {"stage": stage.value}, self._clock())

    def _record_progress(self, job_id: str, embedded_count: int, to_embed_count: int) -> None:
        fields = {"embedded_count": embedded_count, "to_embed_count": to_embed_count}
        self._job_store.update(job_id, fields, self._clock())

    def _finish(self, job_id: str, fields: dict[str, Any]) -> None:
        now = self._clock()
        try:
            self._job_store.update(job_id, {**fields, "finished_at": now}, now)
        except PyMongoError as error:
            logger.error("Could not record the end of job %s: %s", job_id, redact_exception(error))

    def _failure_fields(self, error: Exception) -> dict[str, Any]:
        if isinstance(error, EmbeddingRequestError) and error.is_daily_quota_exhausted:
            now = self._clock()
            return {
                "status": JobStatus.WAITING_FOR_QUOTA.value,
                "error_message": QUOTA_MESSAGE,
                "resume_after": now + timedelta(seconds=seconds_until_daily_quota_reset(now)),
            }
        for error_type, error_code in REFUSAL_CODES:
            if isinstance(error, error_type):
                return failed_fields(error_code, redact(str(error)))
        if isinstance(error, CloneFailedError):
            logger.warning("Clone failed: %s", redact_exception(error))
            return failed_fields(JobErrorCode.CLONE_FAILED, CLONE_FAILED_MESSAGE)
        details = "".join(traceback.format_exception(error))
        logger.error("Indexing job failed: %s", redact(details))
        return failed_fields(JobErrorCode.UNEXPECTED, UNEXPECTED_MESSAGE)

    def _remove_checkouts(self, cloned_paths: list[Path]) -> None:
        for cloned_path in cloned_paths:
            try:
                remove_directory_inside(cloned_path, self._repositories_directory)
            except (OSError, UnsafeDeletionError) as error:
                logger.error("Could not delete a checkout: %s", redact_exception(error))


def succeeded_fields(indexing_run: RepositoryIndexingRun) -> dict[str, Any]:
    report = indexing_run.report
    return {
        "status": JobStatus.SUCCEEDED.value,
        "embedded_count": report.embedded_input_count,
        "result": {
            "commit_id": indexing_run.metadata.commit_id,
            "license_spdx_id": indexing_run.metadata.license_spdx_id,
            "indexed_file_count": indexing_run.indexed_file_count,
            "secret_finding_count": indexing_run.secret_finding_count,
            "call_graph_edge_count": indexing_run.call_graph_edge_count,
            "chunk_count": report.chunk_count,
            "cache_hit_count": report.cache_hit_count,
            "embedded_input_count": report.embedded_input_count,
            "embedding_request_count": report.embedding_request_count,
            "retry_count": report.retry_count,
            "truncated_chunk_count": report.truncated_chunk_count,
            "deleted_stale_chunk_count": report.deleted_stale_chunk_count,
            **refresh_result_fields(indexing_run),
            "rate_limit_wait_seconds": round(report.rate_limit_wait_seconds, 1),
        },
        "timings_ms": {
            stage: round(milliseconds) for stage, milliseconds in indexing_run.timings.items()
        },
    }


def refresh_result_fields(indexing_run: RepositoryIndexingRun) -> dict[str, Any]:
    """Say whether the stored index was patched or indexed in full (and why), and what changed."""
    refresh = indexing_run.refresh
    report = indexing_run.report
    comparison = refresh.comparison
    return {
        "index_mode": "full" if comparison is None else "incremental",
        "full_index_reason": None
        if refresh.full_index_reason is None
        else str(refresh.full_index_reason),
        "added_file_count": None if comparison is None else len(comparison.added),
        "changed_file_count": None if comparison is None else len(comparison.changed),
        "removed_file_count": None if comparison is None else len(comparison.removed),
        "written_chunk_count": report.written_chunk_count,
        "kept_chunk_count": report.kept_chunk_count,
        "updated_calls_count": report.updated_calls_count,
    }


def failed_fields(error_code: JobErrorCode, message: str) -> dict[str, Any]:
    return {
        "status": JobStatus.FAILED.value,
        "error_code": error_code.value,
        "error_message": message,
    }


def start_daemon_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="indexing-job", daemon=True).start()


def build_indexing_job_runner(database: Database, gemini_client: genai.Client) -> IndexingJobRunner:
    """Wire the real job store, metered embedder, and indexing lane; mark interrupted jobs."""
    job_store = MongoJobStore(database)
    job_store.ensure_indexes()
    interrupted_count = job_store.mark_interrupted(datetime.now(UTC))
    if interrupted_count:
        logger.warning("Marked %d indexing jobs as interrupted", interrupted_count)
    return IndexingJobRunner(
        database,
        job_store,
        MongoChunkStore(database),
        build_document_embedder(database, gemini_client),
        build_indexing_rate_limiter(),
    )
