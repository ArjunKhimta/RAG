from __future__ import annotations

from dataclasses import replace

import pytest

from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.fingerprint_comparison import FingerprintComparison
from retrieval.fingerprint_store import StoredFingerprints
from retrieval.fingerprints import build_version_fingerprints
from retrieval.refresh_planning import (
    FullIndexReason,
    plan_kept_chunks,
    usable_fingerprints_or_reason,
)

FORMAT_VERSION = 1

COMMIT_ID = "a" * 40


class FakeEmbedder:
    model_id = "fake-model"
    dimensions = 3
    task_type = "RETRIEVAL_DOCUMENT"


RECORD = {"commit_id": COMMIT_ID, "embedding_model": "fake-model", "embedding_dimensions": 3}

STORED = StoredFingerprints(
    repository="owner/project",
    version="main",
    commit_id=COMMIT_ID,
    format_version=FORMAT_VERSION,
    fingerprints=build_version_fingerprints([]),
)

NO_CHANGES = FingerprintComparison(
    added=(),
    changed=(),
    removed=(),
    unchanged_file_count=2,
    skipped_folder_count=1,
    opened_folder_count=0,
)


def _chunk(file_path: str, name: str, calls: tuple[str, ...] = ()) -> CodeChunk:
    return CodeChunk(
        file_path=file_path,
        start_line=1,
        end_line=2,
        kind=ChunkKind.FUNCTION,
        name=name,
        qualified_name=name,
        parent_class=None,
        text=f"def {name}():\n    pass",
        calls=calls,
    )


def test_matching_record_and_fingerprints_can_be_patched():
    assert usable_fingerprints_or_reason(RECORD, STORED, FakeEmbedder(), FORMAT_VERSION) is STORED


@pytest.mark.parametrize(
    ("record", "stored", "reason"),
    [
        (None, STORED, FullIndexReason.NOT_INDEXED_BEFORE),
        ({**RECORD, "embedding_model": "other"}, STORED, FullIndexReason.OTHER_EMBEDDING),
        ({**RECORD, "embedding_dimensions": 1536}, STORED, FullIndexReason.OTHER_EMBEDDING),
        (RECORD, None, FullIndexReason.NO_FINGERPRINTS),
        (RECORD, replace(STORED, format_version=0), FullIndexReason.OTHER_FORMAT),
        (RECORD, replace(STORED, commit_id="b" * 40), FullIndexReason.OTHER_COMMIT),
    ],
)
def test_anything_out_of_step_means_a_full_index_with_the_reason(record, stored, reason):
    assert usable_fingerprints_or_reason(record, stored, FakeEmbedder(), FORMAT_VERSION) == reason


def test_stored_chunks_of_unchanged_files_are_kept_and_changed_files_are_rewritten():
    chunks = [_chunk("pkg/app.py", "run"), _chunk("pkg/cli.py", "main")]
    comparison = replace(NO_CHANGES, changed=("pkg/cli.py",), unchanged_file_count=1)
    stored_calls = {"run-id": [], "main-id": []}

    kept = plan_kept_chunks(chunks, ["run-id", "main-id"], comparison, stored_calls)

    assert kept.chunk_ids == frozenset({"run-id"})
    assert kept.calls_to_update == {}


def test_chunks_of_added_files_are_never_kept_even_if_an_id_is_stored():
    chunks = [_chunk("pkg/new.py", "fresh")]
    comparison = replace(NO_CHANGES, added=("pkg/new.py",))

    kept = plan_kept_chunks(chunks, ["fresh-id"], comparison, {"fresh-id": []})

    assert kept.chunk_ids == frozenset()


def test_a_kept_chunk_whose_calls_changed_gets_its_new_calls():
    chunks = [_chunk("pkg/app.py", "run", calls=("pkg/helpers.py::helper_v2",))]

    kept = plan_kept_chunks(chunks, ["run-id"], NO_CHANGES, {"run-id": ["pkg/helpers.py::helper"]})

    assert kept.chunk_ids == frozenset({"run-id"})
    assert kept.calls_to_update == {"run-id": ["pkg/helpers.py::helper_v2"]}


def test_a_chunk_of_an_unchanged_file_that_is_not_stored_is_not_kept():
    chunks = [_chunk("pkg/app.py", "run"), _chunk("pkg/app.py", "stop")]

    kept = plan_kept_chunks(chunks, ["run-id", "stop-id"], NO_CHANGES, {"run-id": []})

    assert kept.chunk_ids == frozenset({"run-id"})
