from __future__ import annotations

import pytest

from retrieval.chunk_statistics import (
    SizeSummary,
    nearest_rank_percentile,
    summarize_chunks,
    summarize_sizes,
)
from retrieval.chunker import MAX_CHUNK_CHARACTERS, ChunkKind, CodeChunk


@pytest.mark.parametrize(
    ("percentile", "expected_value"),
    [(1, 10), (10, 10), (11, 20), (50, 50), (90, 90), (91, 100), (99, 100), (100, 100)],
)
def test_nearest_rank_percentile_returns_a_value_that_occurs(percentile, expected_value):
    sorted_values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]

    assert nearest_rank_percentile(sorted_values, percentile) == expected_value


def test_a_single_value_is_every_percentile():
    assert nearest_rank_percentile([7], 1) == 7
    assert nearest_rank_percentile([7], 99) == 7


def test_size_summary_of_unsorted_sizes():
    assert summarize_sizes([30, 10, 20]) == SizeSummary(
        count=3, minimum=10, median=20, percentile_90=30, percentile_99=30, maximum=30
    )


def test_no_sizes_give_no_summary():
    assert summarize_sizes([]) is None


def test_chunks_are_counted_by_kind_separately_for_source_and_test_files():
    chunks = [
        _chunk(ChunkKind.FUNCTION, size=10),
        _chunk(ChunkKind.METHOD, size=20),
        _chunk(ChunkKind.FUNCTION, size=30, is_test_file=True),
    ]

    statistics = summarize_chunks(chunks)

    assert statistics.source_kind_counts == {ChunkKind.FUNCTION: 1, ChunkKind.METHOD: 1}
    assert statistics.test_kind_counts == {ChunkKind.FUNCTION: 1}
    assert statistics.source_sizes.maximum == 20
    assert statistics.test_sizes.maximum == 30
    assert statistics.all_sizes.count == 3


def test_split_definitions_are_counted_once_and_parts_individually():
    chunks = [
        _chunk(ChunkKind.FUNCTION, size=10, part_number=1, part_count=3),
        _chunk(ChunkKind.FUNCTION, size=10, part_number=2, part_count=3),
        _chunk(ChunkKind.FUNCTION, size=10, part_number=3, part_count=3),
        _chunk(ChunkKind.METHOD, size=10, part_number=1, part_count=2),
        _chunk(ChunkKind.METHOD, size=10, part_number=2, part_count=2),
        _chunk(ChunkKind.CLASS, size=10),
    ]

    statistics = summarize_chunks(chunks)

    assert statistics.split_definition_count == 2
    assert statistics.split_part_count == 5


def test_chunks_over_the_limit_and_the_largest_chunks_are_listed():
    oversized_chunk = _chunk(ChunkKind.MODULE, size=MAX_CHUNK_CHARACTERS + 1)
    chunks = [_chunk(ChunkKind.FUNCTION, size=size) for size in range(1, 15)] + [oversized_chunk]

    statistics = summarize_chunks(chunks)

    assert statistics.over_limit_chunks == [oversized_chunk]
    assert statistics.largest_chunks[0] == oversized_chunk
    assert [len(chunk.text) for chunk in statistics.largest_chunks[1:4]] == [14, 13, 12]
    assert len(statistics.largest_chunks) == 10


def _chunk(
    kind: ChunkKind,
    size: int,
    is_test_file: bool = False,
    part_number: int = 1,
    part_count: int = 1,
) -> CodeChunk:
    return CodeChunk(
        file_path="pkg/module.py",
        start_line=1,
        end_line=1,
        kind=kind,
        name="name",
        qualified_name="name",
        parent_class=None,
        text="x" * size,
        part_number=part_number,
        part_count=part_count,
        is_test_file=is_test_file,
    )
