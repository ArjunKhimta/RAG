"""Clone a GitHub repository at a version, chunk its Python files, and print chunk statistics.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/chunk_repository.py https://github.com/pallets/flask --version 3.1.3

An existing clone of that version is reused. Each file is scanned and its secrets redacted before
chunking; the report lists where secrets were found and of what type, never their values. Nothing
is written to MongoDB. Exits 0 on success and 1 when the URL, the version, or the repository is
refused. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter

from retrieval.chunk_statistics import ChunkStatistics, SizeSummary, summarize_chunks
from retrieval.chunker import MAX_CHUNK_CHARACTERS, ChunkKind, CodeChunk
from retrieval.github_urls import InvalidRepositoryUrlError, parse_github_url
from retrieval.redaction import redact
from retrieval.repository_cloner import ClonedRepository, RepositoryError, clone_repository
from retrieval.repository_walker import (
    ChunkingResult,
    SkippedPath,
    WalkResult,
    chunk_source_files,
    find_python_files,
    is_test_path,
)

KIND_COLUMN_WIDTH = 10

COUNT_COLUMN_WIDTH = 8

MILLISECONDS_PER_SECOND = 1000


def main() -> int:
    arguments = _parse_arguments()
    try:
        reference = parse_github_url(arguments.url)
        clone_started = time.perf_counter()
        cloned = clone_repository(reference, arguments.version)
        clone_milliseconds = _milliseconds_since(clone_started)
    except (InvalidRepositoryUrlError, RepositoryError) as error:
        _print(f"Refused: {error}")
        return 1
    walk_started = time.perf_counter()
    walk_result = find_python_files(cloned.source_path)
    walk_milliseconds = _milliseconds_since(walk_started)
    chunk_started = time.perf_counter()
    chunking = chunk_source_files(walk_result.files)
    chunk_milliseconds = _milliseconds_since(chunk_started)
    statistics = summarize_chunks(chunking.chunks)
    report_lines = [
        *_repository_lines(cloned),
        *_file_lines(walk_result, chunking.unscannable),
        *_secret_lines(chunking),
        *_kind_count_lines(statistics),
        *_size_lines(statistics),
        *_split_lines(statistics),
        *_largest_chunk_lines(statistics.largest_chunks),
        *_timing_lines(cloned, clone_milliseconds, walk_milliseconds, chunk_milliseconds),
    ]
    for line in report_lines:
        _print(line)
    return 0


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="https://github.com/<owner>/<repo>")
    parser.add_argument("--version", required=True, help="tag, branch, or commit ID to clone")
    return parser.parse_args()


def _repository_lines(cloned: ClonedRepository) -> list[str]:
    metadata = cloned.metadata
    return [
        f"Repository  {metadata.repository} at {metadata.version}",
        f"Commit      {metadata.commit_id}",
        f"License     {metadata.license_spdx_id} ({metadata.license_name})",
        f"Checkout    {cloned.source_path}",
        "",
    ]


def _file_lines(walk_result: WalkResult, unscannable: list[SkippedPath]) -> list[str]:
    test_file_count = sum(
        1 for source_file in walk_result.files if is_test_path(source_file.relative_path)
    )
    source_file_count = len(walk_result.files) - test_file_count
    skipped_paths = sorted([*walk_result.skipped, *unscannable], key=_relative_path_of)
    lines = [
        f"Python files found: {len(walk_result.files)} "
        f"({source_file_count} source, {test_file_count} test)",
        f"Skipped: {len(skipped_paths)}",
    ]
    for skipped_path in skipped_paths:
        lines.append(f"  {skipped_path.relative_path}  ({skipped_path.reason})")
    lines.append("")
    return lines


def _secret_lines(chunking: ChunkingResult) -> list[str]:
    findings = chunking.secret_findings
    files_with_findings = {finding.file_path for finding in findings}
    redacted_chunk_count = sum(1 for chunk in chunking.chunks if chunk.contains_redaction)
    type_counts = Counter(finding.secret_type for finding in findings)
    lines = [
        f"Secret findings redacted: {len(findings)} in {len(files_with_findings)} files, "
        f"affecting {redacted_chunk_count} chunks",
    ]
    for secret_type, count in type_counts.most_common():
        lines.append(f"  {count:4}  {secret_type}")
    for finding in findings:
        lines.append(f"  {finding.file_path}:{finding.line_number}  {finding.secret_type}")
    lines.append("")
    return lines


def _kind_count_lines(statistics: ChunkStatistics) -> list[str]:
    lines = [_count_row("Chunks", "source", "test", "total")]
    for kind in ChunkKind:
        lines.append(
            _count_row(
                kind,
                statistics.source_kind_counts[kind],
                statistics.test_kind_counts[kind],
                statistics.source_kind_counts[kind] + statistics.test_kind_counts[kind],
            )
        )
    source_total = _total(statistics.source_kind_counts)
    test_total = _total(statistics.test_kind_counts)
    lines.append(_count_row("all", source_total, test_total, source_total + test_total))
    lines.append("")
    return lines


def _size_lines(statistics: ChunkStatistics) -> list[str]:
    return [
        f"Chunk sizes in characters (limit {MAX_CHUNK_CHARACTERS})",
        _size_row("source", statistics.source_sizes),
        _size_row("test", statistics.test_sizes),
        _size_row("all", statistics.all_sizes),
        "",
    ]


def _split_lines(statistics: ChunkStatistics) -> list[str]:
    lines = [
        f"Split definitions: {statistics.split_definition_count} "
        f"(into {statistics.split_part_count} parts)",
        f"Chunks still over the limit: {len(statistics.over_limit_chunks)}",
    ]
    for chunk in statistics.over_limit_chunks:
        lines.append(f"  {_describe_chunk(chunk)}")
    lines.append("")
    return lines


def _largest_chunk_lines(largest_chunks: list[CodeChunk]) -> list[str]:
    lines = [f"Largest {len(largest_chunks)} chunks"]
    for chunk in largest_chunks:
        lines.append(f"  {_describe_chunk(chunk)}")
    lines.append("")
    return lines


def _timing_lines(
    cloned: ClonedRepository,
    clone_milliseconds: float,
    walk_milliseconds: float,
    chunk_milliseconds: float,
) -> list[str]:
    clone_label = "reuse clone" if cloned.was_reused else "clone"
    return [
        "Timing",
        f"  {clone_label.ljust(12)}{clone_milliseconds:8.0f} ms",
        f"  {'walk'.ljust(12)}{walk_milliseconds:8.0f} ms",
        f"  {'scan, chunk'.ljust(12)}{chunk_milliseconds:8.0f} ms",
    ]


def _count_row(label: str, source: object, test: object, total: object) -> str:
    return (
        f"{str(label).ljust(KIND_COLUMN_WIDTH)}"
        f"{str(source).rjust(COUNT_COLUMN_WIDTH)}"
        f"{str(test).rjust(COUNT_COLUMN_WIDTH)}"
        f"{str(total).rjust(COUNT_COLUMN_WIDTH)}"
    )


def _size_row(label: str, sizes: SizeSummary | None) -> str:
    if sizes is None:
        return f"  {label.ljust(8)}no chunks"
    return (
        f"  {label.ljust(8)}count {sizes.count:5}  min {sizes.minimum:5}  "
        f"median {sizes.median:5}  p90 {sizes.percentile_90:5}  "
        f"p99 {sizes.percentile_99:5}  max {sizes.maximum:5}"
    )


def _describe_chunk(chunk: CodeChunk) -> str:
    part_label = ""
    if chunk.part_count > 1:
        part_label = f" part {chunk.part_number}/{chunk.part_count}"
    location = f"{chunk.file_path}:{chunk.start_line}-{chunk.end_line}"
    return f"{len(chunk.text):6} chars  {location}  {chunk.kind} {chunk.qualified_name}{part_label}"


def _relative_path_of(skipped_path: SkippedPath) -> str:
    return skipped_path.relative_path


def _total(kind_counts: Counter[ChunkKind]) -> int:
    return sum(kind_counts.values())


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
