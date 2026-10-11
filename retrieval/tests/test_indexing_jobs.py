"""Tests for the indexing job runner, with an in-memory job store, a fake indexing step, and a
thread starter that runs work only when the test says so, so nothing reaches the network."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from retrieval.embedding_budget import EmbeddingBudgetExhaustedError
from retrieval.fingerprint_comparison import FingerprintComparison
from retrieval.fingerprints import build_version_fingerprints
from retrieval.github_urls import RepositoryReference
from retrieval.indexer import IndexingReport, KeptChunks
from retrieval.indexing_jobs import (
    CLONE_FAILED_MESSAGE,
    UNEXPECTED_MESSAGE,
    IndexingJobRunner,
    JobErrorCode,
    JobStatus,
    StartKind,
    interrupted_fields,
    new_job_document,
    refresh_result_fields,
)
from retrieval.refresh_planning import FullIndexReason, RefreshPlan
from retrieval.repository_cloner import (
    ClonedRepository,
    CloneFailedError,
    CloneMetadata,
    MissingLicenseFileError,
    RepositoryNotFoundError,
    RepositoryNotPublicError,
    RepositoryTooLargeError,
    UnacceptableLicenseError,
    VersionNotFoundError,
)
from retrieval.repository_indexing import (
    IndexingStage,
    NoPythonCodeError,
    RepositoryIndexingRun,
    TooManyChunksError,
)
from retrieval.search_indexes import SearchIndexChange

NOON_PACIFIC = datetime(2026, 10, 5, 19, 0, tzinfo=UTC)

NEXT_MIDNIGHT_PACIFIC = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)

METADATA = CloneMetadata(
    repository="pallets/itsdangerous",
    version="2.2.0",
    commit_id="a" * 40,
    license_spdx_id="BSD-3-Clause",
    license_name="BSD 3-Clause License",
    reported_size_bytes=1,
    checkout_size_bytes=1,
    cloned_at="2026-10-05T00:00:00+00:00",
    license_path="LICENSE.txt",
)

INDEXING_RUN = RepositoryIndexingRun(
    metadata=METADATA,
    indexed_file_count=12,
    skipped_path_count=0,
    secret_finding_count=1,
    resolved_call_count=40,
    call_site_count=200,
    call_graph_edge_count=35,
    vector_index_change=SearchIndexChange.UNCHANGED,
    keyword_index_change=SearchIndexChange.UNCHANGED,
    report=IndexingReport(
        chunk_count=180,
        unique_input_count=180,
        cache_hit_count=20,
        embedded_input_count=160,
        embedding_request_count=2,
        rate_limit_wait_seconds=12.34,
    ),
    collection_statistics={},
    timings={"clone or reuse": 812.4, "embed and store": 30_100.6},
    fingerprints=build_version_fingerprints([]),
    refresh=RefreshPlan(
        full_index_reason=FullIndexReason.NOT_INDEXED_BEFORE, comparison=None, kept=None
    ),
)


class InMemoryJobStore:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.updates: list[dict] = []

    def create(self, job_id, repository, version, now) -> None:
        self.jobs[job_id] = new_job_document(job_id, repository, version, now)

    def update(self, job_id, fields, now) -> None:
        self.updates.append(dict(fields))
        self.jobs[job_id].update({**fields, "updated_at": now})

    def find(self, job_id):
        return self.jobs.get(job_id)

    def mark_interrupted(self, now) -> int:
        running = [job for job in self.jobs.values() if job["status"] == JobStatus.RUNNING]
        for job in running:
            job.update(interrupted_fields(now))
        return len(running)


class Records:
    def __init__(self, records: dict | None = None) -> None:
        self.records = records or {}

    def find_repository_record(self, repository, version):
        return self.records.get((repository, version))


class DeferredThreads:
    def __init__(self) -> None:
        self.pending: list = []

    def __call__(self, work) -> None:
        self.pending.append(work)

    def run_all(self) -> None:
        while self.pending:
            self.pending.pop(0)()


class FakeIndexing:
    """Stands in for index_repository_version: clones, reports stages and progress, then ends."""

    def __init__(self, outcome=INDEXING_RUN) -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    def __call__(self, reference, version, database, embedder, rate_limiter, **hooks):
        self.calls.append({"reference": reference, "version": version, **hooks})
        hooks["on_stage"](IndexingStage.CLONING)
        hooks["clone"](reference, version)
        hooks["on_stage"](IndexingStage.EMBEDDING)
        hooks["on_progress"](0, 160)
        hooks["on_progress"](80, 160)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeClone:
    def __init__(self, repositories_directory: Path, was_reused: bool = False) -> None:
        self.repositories_directory = repositories_directory
        self.was_reused = was_reused
        self.expected_commits: list[str | None] = []

    def __call__(
        self, reference: RepositoryReference, version: str, expected_commit: str | None = None
    ) -> ClonedRepository:
        self.expected_commits.append(expected_commit)
        version_directory = self.repositories_directory / reference.owner / reference.name / version
        source_path = version_directory / "source"
        source_path.mkdir(parents=True, exist_ok=True)
        (source_path / "module.py").write_text("def run():\n    pass\n")
        return ClonedRepository(
            path=version_directory,
            source_path=source_path,
            metadata=METADATA,
            was_reused=self.was_reused,
        )


class FakeRemote:
    def __init__(self, commit_or_error: str | Exception) -> None:
        self.commit_or_error = commit_or_error
        self.lookups: list[tuple[RepositoryReference, str]] = []

    def __call__(self, reference: RepositoryReference, version: str) -> str:
        self.lookups.append((reference, version))
        if isinstance(self.commit_or_error, Exception):
            raise self.commit_or_error
        return self.commit_or_error


def _runner(
    tmp_path, indexing=None, records=None, clone=None, threads=None, store=None, remote=None
):
    job_ids = iter(f"job-{number}" for number in range(1, 100))
    return IndexingJobRunner(
        database="database",
        job_store=store or InMemoryJobStore(),
        repository_records=records or Records(),
        embedder="embedder",
        rate_limiter="rate limiter",
        index_version=indexing or FakeIndexing(),
        clone=clone or FakeClone(tmp_path),
        find_remote_commit=remote or FakeRemote(AssertionError("GitHub must not be asked")),
        start_thread=threads or DeferredThreads(),
        repositories_directory=tmp_path,
        clock=lambda: NOON_PACIFIC,
        new_job_id=lambda: next(job_ids),
    )


def test_an_indexed_version_needs_no_job(tmp_path):
    record = {"commit_id": "c" * 40}
    store = InMemoryJobStore()
    runner = _runner(tmp_path, records=Records({("pallets/flask", "3.1.3"): record}), store=store)

    outcome = runner.start("pallets/flask", "3.1.3")

    assert outcome.kind == StartKind.ALREADY_INDEXED
    assert outcome.repository_record == record
    assert store.jobs == {}


def test_a_started_job_is_running_until_its_thread_runs_then_succeeds(tmp_path):
    threads = DeferredThreads()
    indexing = FakeIndexing()
    store = InMemoryJobStore()
    runner = _runner(tmp_path, indexing=indexing, threads=threads, store=store)

    outcome = runner.start("pallets/itsdangerous", "2.2.0")

    assert outcome.kind == StartKind.STARTED
    assert outcome.job_id == "job-1"
    assert runner.find_job("job-1")["status"] == JobStatus.RUNNING
    threads.run_all()
    job = runner.find_job("job-1")
    assert job["status"] == JobStatus.SUCCEEDED
    assert job["stage"] == "embedding"
    assert (job["embedded_count"], job["to_embed_count"]) == (160, 160)
    assert job["result"]["chunk_count"] == 180
    assert job["result"]["cache_hit_count"] == 20
    assert job["result"]["commit_id"] == "a" * 40
    assert job["result"]["rate_limit_wait_seconds"] == 12.3
    assert job["result"]["index_mode"] == "full"
    assert job["result"]["full_index_reason"] == "not indexed before"
    assert job["result"]["changed_file_count"] is None
    assert job["timings_ms"] == {"clone or reuse": 812, "embed and store": 30101}
    assert job["finished_at"] == NOON_PACIFIC
    assert job["error_code"] is None
    assert indexing.calls[0]["reference"] == RepositoryReference("pallets", "itsdangerous")
    assert {"stage": "cloning"} in store.updates
    assert {"embedded_count": 80, "to_embed_count": 160} in store.updates


def test_one_job_runs_at_a_time_and_the_same_version_gets_the_same_job(tmp_path):
    threads = DeferredThreads()
    runner = _runner(tmp_path, threads=threads)
    runner.start("pallets/itsdangerous", "2.2.0")

    same = runner.start("pallets/itsdangerous", "2.2.0")
    other = runner.start("pallets/click", "8.1.7")

    assert (same.kind, same.job_id) == (StartKind.ALREADY_RUNNING, "job-1")
    assert (other.kind, other.job_id) == (StartKind.BUSY, None)
    threads.run_all()
    assert runner.start("pallets/click", "8.1.7").kind == StartKind.STARTED


def test_a_used_up_budget_waits_for_the_next_midnight_pacific(tmp_path):
    threads = DeferredThreads()
    runner = _runner(
        tmp_path, indexing=FakeIndexing(EmbeddingBudgetExhaustedError("No room")), threads=threads
    )

    runner.start("pallets/itsdangerous", "2.2.0")
    threads.run_all()

    job = runner.find_job("job-1")
    assert job["status"] == JobStatus.WAITING_FOR_QUOTA
    assert job["resume_after"] == NEXT_MIDNIGHT_PACIFIC
    assert job["embedded_count"] == 80
    assert "resume_after" in job["error_message"]
    assert runner.start("pallets/itsdangerous", "2.2.0").kind == StartKind.STARTED


@pytest.mark.parametrize(
    "error, code",
    [
        (RepositoryNotFoundError("pallets/nothing was not found"), JobErrorCode.NOT_FOUND),
        (RepositoryNotPublicError("pallets/secret is private"), JobErrorCode.NOT_PUBLIC),
        (UnacceptableLicenseError("GPL-3.0 is not allowed"), JobErrorCode.LICENSE_NOT_ALLOWED),
        (MissingLicenseFileError("no license file"), JobErrorCode.LICENSE_NOT_ALLOWED),
        (RepositoryTooLargeError("over 100 MB"), JobErrorCode.TOO_LARGE),
        (TooManyChunksError("has 4,210 chunks"), JobErrorCode.TOO_MANY_CHUNKS),
        (NoPythonCodeError("has no Python code to index"), JobErrorCode.NO_PYTHON_CODE),
    ],
)
def test_each_refusal_fails_with_its_code_and_message(tmp_path, error, code):
    threads = DeferredThreads()
    runner = _runner(tmp_path, indexing=FakeIndexing(error), threads=threads)

    runner.start("pallets/itsdangerous", "2.2.0")
    threads.run_all()

    job = runner.find_job("job-1")
    assert job["status"] == JobStatus.FAILED
    assert job["error_code"] == code
    assert job["error_message"] == str(error)


def test_a_failed_clone_hides_git_output(tmp_path):
    threads = DeferredThreads()
    git_output = CloneFailedError("git fetch failed: fatal: couldn't find remote ref 9.9.9")
    runner = _runner(tmp_path, indexing=FakeIndexing(git_output), threads=threads)

    runner.start("pallets/itsdangerous", "9.9.9")
    threads.run_all()

    job = runner.find_job("job-1")
    assert job["error_code"] == JobErrorCode.CLONE_FAILED
    assert job["error_message"] == CLONE_FAILED_MESSAGE


def test_an_unexpected_error_is_logged_redacted_and_never_shown(tmp_path, caplog):
    threads = DeferredThreads()
    leak = RuntimeError("mongodb+srv://user:password1234@cluster refused")
    runner = _runner(tmp_path, indexing=FakeIndexing(leak), threads=threads)

    runner.start("pallets/itsdangerous", "2.2.0")
    threads.run_all()

    job = runner.find_job("job-1")
    assert job["error_code"] == JobErrorCode.UNEXPECTED
    assert job["error_message"] == UNEXPECTED_MESSAGE
    assert "password1234" not in str(job)
    assert "password1234" not in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.parametrize("outcome", [INDEXING_RUN, RepositoryTooLargeError("over 100 MB")])
def test_a_checkout_the_job_cloned_is_deleted_whatever_the_outcome(tmp_path, outcome):
    threads = DeferredThreads()
    runner = _runner(tmp_path, indexing=FakeIndexing(outcome), threads=threads)

    runner.start("pallets/itsdangerous", "2.2.0")
    threads.run_all()

    assert not (tmp_path / "pallets").exists()
    assert tmp_path.is_dir()


def test_a_checkout_that_already_existed_is_kept(tmp_path):
    threads = DeferredThreads()
    runner = _runner(tmp_path, clone=FakeClone(tmp_path, was_reused=True), threads=threads)

    runner.start("pallets/itsdangerous", "2.2.0")
    threads.run_all()

    assert (tmp_path / "pallets" / "itsdangerous" / "2.2.0" / "source" / "module.py").is_file()


def test_a_checkout_outside_the_repositories_folder_is_never_deleted(tmp_path, caplog):
    threads = DeferredThreads()
    elsewhere = tmp_path / "elsewhere"
    runner = _runner(tmp_path / "repos", clone=FakeClone(elsewhere), threads=threads)

    runner.start("pallets/itsdangerous", "2.2.0")
    threads.run_all()

    assert (elsewhere / "pallets" / "itsdangerous" / "2.2.0").is_dir()
    assert "Could not delete a checkout" in caplog.text
    assert runner.start("pallets/click", "8.1.7").kind == StartKind.STARTED


def test_a_job_that_cannot_be_saved_does_not_block_the_next(tmp_path):
    class FailingOnceStore(InMemoryJobStore):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 1

        def create(self, job_id, repository, version, now) -> None:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("database unavailable")
            super().create(job_id, repository, version, now)

    runner = _runner(tmp_path, store=FailingOnceStore())

    with pytest.raises(ConnectionError):
        runner.start("pallets/itsdangerous", "2.2.0")

    assert runner.start("pallets/itsdangerous", "2.2.0").kind == StartKind.STARTED


def test_job_ids_are_random_and_unguessable(tmp_path):
    runner = IndexingJobRunner(
        database="database",
        job_store=InMemoryJobStore(),
        repository_records=Records(),
        embedder="embedder",
        rate_limiter="rate limiter",
        index_version=FakeIndexing(),
        clone=FakeClone(tmp_path),
        start_thread=DeferredThreads(),
        repositories_directory=tmp_path,
    )

    job_id = runner.start("pallets/itsdangerous", "2.2.0").job_id

    assert len(job_id) == 32
    assert int(job_id, 16) >= 0


def test_a_patched_version_reports_what_changed_and_what_was_kept():
    comparison = FingerprintComparison(
        added=("pkg/new.py",),
        changed=("pkg/app.py", "pkg/cli.py"),
        removed=(),
        unchanged_file_count=9,
        skipped_folder_count=2,
        opened_folder_count=2,
    )
    report = replace(
        INDEXING_RUN.report, written_chunk_count=14, kept_chunk_count=166, updated_calls_count=3
    )
    patched_run = replace(
        INDEXING_RUN,
        report=report,
        refresh=RefreshPlan(
            full_index_reason=None,
            comparison=comparison,
            kept=KeptChunks(chunk_ids=frozenset(), calls_to_update={}),
        ),
    )

    assert refresh_result_fields(patched_run) == {
        "index_mode": "incremental",
        "full_index_reason": None,
        "added_file_count": 1,
        "changed_file_count": 2,
        "removed_file_count": 0,
        "written_chunk_count": 14,
        "kept_chunk_count": 166,
        "updated_calls_count": 3,
    }


INDEXED_RECORD = {"commit_id": "c" * 40}

INDEXED_FLASK = {("pallets/flask", "main"): INDEXED_RECORD}


def test_refreshing_a_version_never_indexed_starts_an_ordinary_job(tmp_path):
    clone = FakeClone(tmp_path)
    threads = DeferredThreads()
    runner = _runner(tmp_path, clone=clone, threads=threads)

    outcome = runner.refresh("pallets/flask", "main")
    threads.run_all()

    assert outcome.kind == StartKind.STARTED
    assert clone.expected_commits == [None]


def test_a_commit_id_version_is_up_to_date_without_asking_github(tmp_path):
    commit_version = "a" * 40
    records = Records({("pallets/flask", commit_version): INDEXED_RECORD})
    store = InMemoryJobStore()
    runner = _runner(tmp_path, records=records, store=store)

    outcome = runner.refresh("pallets/flask", commit_version)

    assert outcome.kind == StartKind.UP_TO_DATE
    assert outcome.repository_record == INDEXED_RECORD
    assert store.jobs == {}


def test_a_branch_still_at_the_indexed_commit_is_up_to_date(tmp_path):
    remote = FakeRemote("c" * 40)
    store = InMemoryJobStore()
    runner = _runner(tmp_path, records=Records(INDEXED_FLASK), store=store, remote=remote)

    outcome = runner.refresh("pallets/flask", "main")

    assert outcome.kind == StartKind.UP_TO_DATE
    assert remote.lookups == [(RepositoryReference("pallets", "flask"), "main")]
    assert store.jobs == {}


def test_a_branch_that_moved_starts_a_job_that_expects_the_new_commit(tmp_path):
    clone = FakeClone(tmp_path)
    threads = DeferredThreads()
    runner = _runner(
        tmp_path,
        records=Records(INDEXED_FLASK),
        clone=clone,
        threads=threads,
        remote=FakeRemote("d" * 40),
    )

    outcome = runner.refresh("pallets/flask", "main")
    threads.run_all()

    assert outcome.kind == StartKind.STARTED
    assert clone.expected_commits == ["d" * 40]
    assert runner.find_job(outcome.job_id)["status"] == JobStatus.SUCCEEDED


def test_a_moved_branch_waits_its_turn_like_any_job(tmp_path):
    threads = DeferredThreads()
    runner = _runner(
        tmp_path, records=Records(INDEXED_FLASK), threads=threads, remote=FakeRemote("d" * 40)
    )
    other = runner.start("pallets/itsdangerous", "2.2.0")

    busy = runner.refresh("pallets/flask", "main")
    threads.run_all()
    started = runner.refresh("pallets/flask", "main")
    again = runner.refresh("pallets/flask", "main")

    assert other.kind == StartKind.STARTED
    assert busy.kind == StartKind.BUSY
    assert started.kind == StartKind.STARTED
    assert again.kind == StartKind.ALREADY_RUNNING
    assert again.job_id == started.job_id


def test_a_branch_that_no_longer_exists_is_reported_and_starts_nothing(tmp_path):
    store = InMemoryJobStore()
    gone = VersionNotFoundError("main is not a tag or branch of pallets/flask")
    runner = _runner(tmp_path, records=Records(INDEXED_FLASK), store=store, remote=FakeRemote(gone))

    with pytest.raises(VersionNotFoundError):
        runner.refresh("pallets/flask", "main")

    assert store.jobs == {}
