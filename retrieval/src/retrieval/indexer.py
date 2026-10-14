"""Embed a repository version's chunks and store them, reusing cached embeddings.

The steps, in order:
1. Build each chunk's embedding input and cache key. Chunks with identical input share one key,
   so each distinct input is embedded at most once.
2. Look the keys up in the store. Chunks whose key is already there are written straight away with
   the stored vector, with no API call.
3. Embed the remaining inputs in batches that fit both the batch size and the per-minute token
   limit of the indexing lane (80 texts and 24,000 tokens of Gemini's 100 and 30,000 a minute,
   leaving the rest for questions), pausing as the rate limiter requires. Batches are formed by
   the token estimate, then counted exactly with one token-count call each and halved until each
   part fits, since a call over the per-minute token limit is refused however long it waits; the
   exact count is what the rate limiter records. Each batch is written as soon as it returns, so
   an interrupted run resumes from the cache.
4. Delete chunks of this version that no longer exist, then record the version.

A refresh passes the chunks it can keep: chunks already stored unchanged from the version's
previous commit. They are counted and kept but never embedded or rewritten; only their call links
that changed are updated, and the whole version is given the new commit ID before the record.

An optional progress hook hears how many inputs still needed embedding after the cache lookup, and
the running count after each batch, so a caller can show "300 of 1,009".

The model silently truncates inputs over its token limit, so an input whose pessimistic estimate
is over the limit has its tokens counted exactly, and if it really is over, its chunks are marked
`embedding_truncated` rather than hidden.

Per-minute rate-limit and server errors are retried with exponential backoff and random jitter,
using the API's suggested delay when it gives one, and so are calls cut off by the client's
30-second limit; each retry and the final give-up are logged with their reason (an HTTP status or
"timed out after 30 s", never a reply body). After the last attempt the error propagates. An
exhausted daily quota is raised at once: it will not free up until the daily reset, and the work
done so far is already stored, so the next run resumes from the cache. When the day's embedding
budget refuses a batch but still has a few seats, the first that many inputs of the batch are
embedded and stored first, so the day's last seats are used rather than left over; the refused
call already took its per-minute slot, so that smaller batch may wait up to a minute for its own.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from retrieval.chunk_store import (
    CachedEmbedding,
    ChunkStore,
    IndexedVersion,
    StoredChunk,
    chunk_id_for,
)
from retrieval.chunker import CodeChunk
from retrieval.code_graph import edge_count_of
from retrieval.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_INPUT_TOKEN_LIMIT,
    INDEXING_EMBEDDING_TOKENS_PER_MINUTE,
    INDEXING_EMBEDDINGS_PER_MINUTE,
)
from retrieval.embedders import DocumentEmbedder, EmbeddingRequestError
from retrieval.embedding_budget import EmbeddingBudgetExhaustedError
from retrieval.embedding_inputs import build_embedding_input, compute_embedding_key, estimate_tokens
from retrieval.gemini_errors import retry_delay_seconds
from retrieval.other_languages import OtherLanguageCount
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact
from retrieval.repository_cloner import CloneMetadata

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6

ProgressHook = Callable[[int, int], None]


@dataclass
class IndexingReport:
    chunk_count: int = 0
    unique_input_count: int = 0
    cache_hit_count: int = 0
    embedded_input_count: int = 0
    embedding_request_count: int = 0
    retry_count: int = 0
    token_count_request_count: int = 0
    batch_token_count_request_count: int = 0
    counted_tokens_sent: int = 0
    truncated_chunk_count: int = 0
    written_chunk_count: int = 0
    kept_chunk_count: int = 0
    updated_calls_count: int = 0
    deleted_stale_chunk_count: int = 0
    rate_limit_wait_seconds: float = 0.0
    retry_wait_seconds: float = 0.0


@dataclass(frozen=True)
class _PendingInput:
    embedding_key: str
    input_text: str
    estimated_tokens: int


@dataclass(frozen=True)
class _CountedBatch:
    inputs: list[_PendingInput]
    token_count: int


@dataclass(frozen=True)
class KeptChunks:
    """Chunks stored unchanged from the version's previous commit, and new calls for those whose
    call links changed."""

    chunk_ids: frozenset[str]
    calls_to_update: dict[str, list[str]]


@dataclass(frozen=True)
class IndexingSettings:
    batch_size: int = min(EMBEDDING_BATCH_SIZE, INDEXING_EMBEDDINGS_PER_MINUTE)
    batch_token_budget: int = INDEXING_EMBEDDING_TOKENS_PER_MINUTE
    input_token_limit: int = EMBEDDING_INPUT_TOKEN_LIMIT
    max_attempts: int = MAX_ATTEMPTS


def index_chunks(
    metadata: CloneMetadata,
    chunks: list[CodeChunk],
    embedder: DocumentEmbedder,
    store: ChunkStore,
    rate_limiter: RateLimiter,
    settings: IndexingSettings | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter_fraction: Callable[[], float] = random.random,
    on_progress: ProgressHook | None = None,
    other_languages: tuple[OtherLanguageCount, ...] = (),
    kept: KeptChunks | None = None,
) -> IndexingReport:
    """Embed and store `chunks` as the given repository version, and return what it cost.

    `on_progress`, if given, is called with (inputs embedded so far, inputs to embed).
    `other_languages` is recorded with the version: the files the walker counted but did not read.
    `kept`, if given, names chunks already stored unchanged, which are kept without being written.
    """
    active_settings = settings or IndexingSettings()
    report = IndexingReport(chunk_count=len(chunks))
    all_chunk_ids = [chunk_id_for(metadata, chunk) for chunk in chunks]
    _require_unique(all_chunk_ids)
    kept_ids = kept.chunk_ids if kept is not None else frozenset()
    positions_to_write = [
        position for position, chunk_id in enumerate(all_chunk_ids) if chunk_id not in kept_ids
    ]
    chunk_ids = [all_chunk_ids[position] for position in positions_to_write]
    chunks_to_write = [chunks[position] for position in positions_to_write]
    report.written_chunk_count = len(chunks_to_write)
    report.kept_chunk_count = len(chunks) - len(chunks_to_write)
    input_texts = [build_embedding_input(chunk) for chunk in chunks_to_write]
    embedding_keys = [compute_embedding_key(embedder, input_text) for input_text in input_texts]
    unique_inputs = dict(zip(embedding_keys, input_texts, strict=True))
    report.unique_input_count = len(unique_inputs)
    chunk_positions_by_key: dict[str, list[int]] = {}
    for position, embedding_key in enumerate(embedding_keys):
        chunk_positions_by_key.setdefault(embedding_key, []).append(position)

    def store_chunks_for(embeddings: dict[str, CachedEmbedding]) -> None:
        stored_chunks = [
            StoredChunk(
                chunk_id=chunk_ids[position],
                metadata=metadata,
                chunk=chunks_to_write[position],
                embedding_key=embedding_key,
                embedding=embedding,
            )
            for embedding_key, embedding in embeddings.items()
            for position in chunk_positions_by_key[embedding_key]
        ]
        report.truncated_chunk_count += sum(
            1 for stored in stored_chunks if stored.embedding.is_truncated
        )
        store.upsert_chunks(stored_chunks)

    cached_embeddings = store.find_cached_embeddings(list(unique_inputs))
    report.cache_hit_count = len(cached_embeddings)
    store_chunks_for(cached_embeddings)
    pending_inputs = [
        _PendingInput(embedding_key, input_text, estimate_tokens(input_text))
        for embedding_key, input_text in unique_inputs.items()
        if embedding_key not in cached_embeddings
    ]

    def embed_and_store(counted_batch: _CountedBatch) -> None:
        batch = counted_batch.inputs
        truncation_flags = [
            _is_over_token_limit(embedder, pending, active_settings.input_token_limit, report)
            for pending in batch
        ]
        vectors = _embed_with_retries(
            embedder,
            counted_batch,
            rate_limiter,
            active_settings,
            sleep,
            jitter_fraction,
            report,
        )
        batch_embeddings = {
            pending.embedding_key: CachedEmbedding(vector=vector, is_truncated=is_truncated)
            for pending, vector, is_truncated in zip(batch, vectors, truncation_flags, strict=True)
        }
        report.embedded_input_count += len(batch)
        store_chunks_for(batch_embeddings)
        if on_progress is not None:
            on_progress(report.embedded_input_count, len(pending_inputs))

    def embed_what_the_budget_allows(
        counted_batch: _CountedBatch, error: EmbeddingBudgetExhaustedError
    ) -> None:
        fitting_inputs = counted_batch.inputs[: error.seats_left]
        if not 0 < len(fitting_inputs) < len(counted_batch.inputs):
            return
        for fitting_batch in _counted_batches(
            embedder, fitting_inputs, active_settings.batch_token_budget, report
        ):
            embed_and_store(fitting_batch)

    if on_progress is not None:
        on_progress(0, len(pending_inputs))
    for estimated_batch in _batches(pending_inputs, active_settings):
        for counted_batch in _counted_batches(
            embedder, estimated_batch, active_settings.batch_token_budget, report
        ):
            try:
                embed_and_store(counted_batch)
            except EmbeddingBudgetExhaustedError as error:
                embed_what_the_budget_allows(counted_batch, error)
                raise
    if kept is not None:
        store.update_chunk_calls(kept.calls_to_update)
        report.updated_calls_count = len(kept.calls_to_update)
        store.set_commit_id(metadata.repository, metadata.version, metadata.commit_id)
    report.deleted_stale_chunk_count = store.delete_chunks_except(
        metadata.repository, metadata.version, set(all_chunk_ids)
    )
    store.upsert_repository(
        IndexedVersion(
            metadata=metadata,
            embedding_model=embedder.model_id,
            embedding_dimensions=embedder.dimensions,
            chunk_count=len(chunks),
            call_graph_edge_count=edge_count_of(chunks),
            other_languages=other_languages,
        )
    )
    return report


def _batches(
    pending_inputs: list[_PendingInput], settings: IndexingSettings
) -> Iterator[list[_PendingInput]]:
    """Group inputs so each batch holds at most `batch_size` texts and fits the token budget."""
    current_batch: list[_PendingInput] = []
    current_tokens = 0
    for pending in pending_inputs:
        is_full = len(current_batch) == settings.batch_size
        tokens_with_pending = current_tokens + pending.estimated_tokens
        would_exceed_budget = tokens_with_pending > settings.batch_token_budget
        if current_batch and (is_full or would_exceed_budget):
            yield current_batch
            current_batch = []
            current_tokens = 0
        current_batch.append(pending)
        current_tokens += pending.estimated_tokens
    if current_batch:
        yield current_batch


def _counted_batches(
    embedder: DocumentEmbedder,
    batch: list[_PendingInput],
    token_budget: int,
    report: IndexingReport,
) -> Iterator[_CountedBatch]:
    """Count a batch's tokens exactly and split it in halves until each part fits the budget.

    The estimate can fall short on unusual text, and a call over the per-minute token limit is
    refused however long it waits. A single input over the budget is yielded as it is, so the
    rate limiter refuses it with its own error.
    """
    report.batch_token_count_request_count += 1
    token_count = embedder.count_batch_tokens([pending.input_text for pending in batch])
    if token_count <= token_budget or len(batch) == 1:
        yield _CountedBatch(inputs=batch, token_count=token_count)
        return
    middle = len(batch) // 2
    yield from _counted_batches(embedder, batch[:middle], token_budget, report)
    yield from _counted_batches(embedder, batch[middle:], token_budget, report)


def _is_over_token_limit(
    embedder: DocumentEmbedder, pending: _PendingInput, token_limit: int, report: IndexingReport
) -> bool:
    """Count tokens exactly only when the pessimistic estimate says the input might be too long."""
    if pending.estimated_tokens <= token_limit:
        return False
    report.token_count_request_count += 1
    return embedder.count_tokens(pending.input_text) > token_limit


def _embed_with_retries(
    embedder: DocumentEmbedder,
    counted_batch: _CountedBatch,
    rate_limiter: RateLimiter,
    settings: IndexingSettings,
    sleep: Callable[[float], None],
    jitter_fraction: Callable[[], float],
    report: IndexingReport,
) -> list[list[float]]:
    batch = counted_batch.inputs
    input_texts = [pending.input_text for pending in batch]
    batch_tokens = counted_batch.token_count
    for attempt_number in range(1, settings.max_attempts + 1):
        report.rate_limit_wait_seconds += rate_limiter.acquire(len(batch), batch_tokens)
        report.embedding_request_count += 1
        try:
            vectors = embedder.embed_documents(input_texts)
        except EmbeddingRequestError as error:
            if not error.is_retryable:
                raise
            if attempt_number == settings.max_attempts:
                logger.warning(
                    "Gemini embedding of %d texts gave up after %d attempts (%s)",
                    len(batch),
                    attempt_number,
                    _failure_description(error),
                )
                raise
            delay_seconds = retry_delay_seconds(error, attempt_number, jitter_fraction)
            logger.warning(
                "Gemini embedding attempt %d of %d for %d texts failed (%s); retrying in %.1f s",
                attempt_number,
                settings.max_attempts,
                len(batch),
                _failure_description(error),
                delay_seconds,
            )
            report.retry_count += 1
            report.retry_wait_seconds += delay_seconds
            sleep(delay_seconds)
            continue
        report.counted_tokens_sent += batch_tokens
        return vectors
    raise AssertionError("unreachable: the last attempt either returns or raises")


def _failure_description(error: EmbeddingRequestError) -> str:
    """The failure's short reason, plus the quotas Gemini says were exceeded, if it names any."""
    if not error.exceeded_quota_ids:
        return redact(error.reason)
    quota_ids = ", ".join(error.exceeded_quota_ids)
    return redact(f"{error.reason}; quota {quota_ids}")


def _require_unique(chunk_ids: list[str]) -> None:
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("Two chunks produced the same chunk ID; refusing to overwrite one")
