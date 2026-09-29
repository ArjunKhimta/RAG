"""Summary statistics over a repository's chunks, used to check the chunk size limit on real code.

Percentiles use the nearest-rank method: the p-th percentile is the smallest size that at least p
percent of chunks are at or below. It always returns a size that actually occurs, which keeps the
numbers easy to check by hand.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from retrieval.chunker import MAX_CHUNK_CHARACTERS, ChunkKind, CodeChunk

LARGEST_CHUNK_COUNT = 10


@dataclass(frozen=True)
class SizeSummary:
    count: int
    minimum: int
    median: int
    percentile_90: int
    percentile_99: int
    maximum: int


@dataclass(frozen=True)
class ChunkStatistics:
    source_kind_counts: Counter[ChunkKind]
    test_kind_counts: Counter[ChunkKind]
    source_sizes: SizeSummary | None
    test_sizes: SizeSummary | None
    all_sizes: SizeSummary | None
    split_definition_count: int
    split_part_count: int
    over_limit_chunks: list[CodeChunk]
    largest_chunks: list[CodeChunk]


def summarize_chunks(chunks: list[CodeChunk]) -> ChunkStatistics:
    source_chunks = [chunk for chunk in chunks if not chunk.is_test_file]
    test_chunks = [chunk for chunk in chunks if chunk.is_test_file]
    split_parts = [chunk for chunk in chunks if chunk.part_count > 1]
    first_split_parts = [chunk for chunk in split_parts if chunk.part_number == 1]
    chunks_by_size = sorted(chunks, key=_chunk_size, reverse=True)
    return ChunkStatistics(
        source_kind_counts=Counter(chunk.kind for chunk in source_chunks),
        test_kind_counts=Counter(chunk.kind for chunk in test_chunks),
        source_sizes=summarize_sizes([_chunk_size(chunk) for chunk in source_chunks]),
        test_sizes=summarize_sizes([_chunk_size(chunk) for chunk in test_chunks]),
        all_sizes=summarize_sizes([_chunk_size(chunk) for chunk in chunks]),
        split_definition_count=len(first_split_parts),
        split_part_count=len(split_parts),
        over_limit_chunks=[chunk for chunk in chunks if _chunk_size(chunk) > MAX_CHUNK_CHARACTERS],
        largest_chunks=chunks_by_size[:LARGEST_CHUNK_COUNT],
    )


def summarize_sizes(sizes: list[int]) -> SizeSummary | None:
    if not sizes:
        return None
    sorted_sizes = sorted(sizes)
    return SizeSummary(
        count=len(sorted_sizes),
        minimum=sorted_sizes[0],
        median=nearest_rank_percentile(sorted_sizes, 50),
        percentile_90=nearest_rank_percentile(sorted_sizes, 90),
        percentile_99=nearest_rank_percentile(sorted_sizes, 99),
        maximum=sorted_sizes[-1],
    )


def nearest_rank_percentile(sorted_values: list[int], percentile: float) -> int:
    """Return the smallest value that at least `percentile` percent of values are at or below."""
    rank = math.ceil(percentile / 100 * len(sorted_values))
    index = max(rank, 1) - 1
    return sorted_values[index]


def _chunk_size(chunk: CodeChunk) -> int:
    return len(chunk.text)
