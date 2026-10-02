"""Embed a repository version's chunks and store them, reusing cached embeddings.

The steps, in order:
1. Build each chunk's embedding input and cache key. Chunks with identical input share one key,
   so each distinct input is embedded at most once.
2. Look the keys up in the store. Chunks whose key is already there are written straight away with
   the stored vector, with no API call.
3. Embed the remaining inputs in batches that fit both the batch size and the per-minute token
   limit, pausing as the rate limiter requires. Each batch is written as soon as it returns, so an
   interrupted run resumes from the cache.
4. Delete chunks of this version that no longer exist, then record the version.

The model silently truncates inputs over its token limit, so an input whose pessimistic estimate
is over the limit has its tokens counted exactly, and if it really is over, its chunks are marked
`embedding_truncated` rather than hidden.

Per-minute rate-limit and server errors are retried with exponential backoff and random jitter,
using the API's suggested delay when it gives one. After the last attempt the error propagates. An
exhausted daily quota is raised at once: it will not free up until the daily reset, and the work
done so far is already stored, so the next run resumes from the cache.
"""

from __future__ import annotations

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
from retrieval.config import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_INPUT_TOKEN_LIMIT,
    EMBEDDING_TOKENS_PER_MINUTE,
)
from retrieval.embedders import DocumentEmbedder, EmbeddingRequestError
from retrieval.embedding_inputs import build_embedding_input, compute_embedding_key, estimate_tokens
from retrieval.gemini_errors import retry_delay_seconds
from retrieval.rate_limiter import RateLimiter
from retrieval.repository_cloner import CloneMetadata

MAX_ATTEMPTS = 6


@dataclass
class IndexingReport:
    chunk_count: int = 0
    unique_input_count: int = 0
    cache_hit_count: int = 0
    embedded_input_count: int = 0
    embedding_request_count: int = 0
    retry_count: int = 0
    token_count_request_count: int = 0
    estimated_tokens_sent: int = 0
    truncated_chunk_count: int = 0
    deleted_stale_chunk_count: int = 0
    rate_limit_wait_seconds: float = 0.0
    retry_wait_seconds: float = 0.0


@dataclass(frozen=True)
class _PendingInput:
    embedding_key: str
    input_text: str
    estimated_tokens: int


@dataclass(frozen=True)
class IndexingSettings:
    batch_size: int = EMBEDDING_BATCH_SIZE
    batch_token_budget: int = EMBEDDING_TOKENS_PER_MINUTE
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
) -> IndexingReport:
    """Embed and store `chunks` as the given repository version, and return what it cost."""
    active_settings = settings or IndexingSettings()
    report = IndexingReport(chunk_count=len(chunks))
    chunk_ids = [chunk_id_for(metadata, chunk) for chunk in chunks]
    _require_unique(chunk_ids)
    input_texts = [build_embedding_input(chunk) for chunk in chunks]
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
                chunk=chunks[position],
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
    for batch in _batches(pending_inputs, active_settings):
        truncation_flags = [
            _is_over_token_limit(embedder, pending, active_settings.input_token_limit, report)
            for pending in batch
        ]
        vectors = _embed_with_retries(
            embedder, batch, rate_limiter, active_settings, sleep, jitter_fraction, report
        )
        batch_embeddings = {
            pending.embedding_key: CachedEmbedding(vector=vector, is_truncated=is_truncated)
            for pending, vector, is_truncated in zip(batch, vectors, truncation_flags, strict=True)
        }
        report.embedded_input_count += len(batch)
        store_chunks_for(batch_embeddings)
    report.deleted_stale_chunk_count = store.delete_chunks_except(
        metadata.repository, metadata.version, set(chunk_ids)
    )
    store.upsert_repository(
        IndexedVersion(
            metadata=metadata,
            embedding_model=embedder.model_id,
            embedding_dimensions=embedder.dimensions,
            chunk_count=len(chunks),
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
    batch: list[_PendingInput],
    rate_limiter: RateLimiter,
    settings: IndexingSettings,
    sleep: Callable[[float], None],
    jitter_fraction: Callable[[], float],
    report: IndexingReport,
) -> list[list[float]]:
    input_texts = [pending.input_text for pending in batch]
    batch_tokens = sum(pending.estimated_tokens for pending in batch)
    for attempt_number in range(1, settings.max_attempts + 1):
        report.rate_limit_wait_seconds += rate_limiter.acquire(len(batch), batch_tokens)
        report.embedding_request_count += 1
        try:
            vectors = embedder.embed_documents(input_texts)
        except EmbeddingRequestError as error:
            is_last_attempt = attempt_number == settings.max_attempts
            if not error.is_retryable or is_last_attempt:
                raise
            delay_seconds = retry_delay_seconds(error, attempt_number, jitter_fraction)
            report.retry_count += 1
            report.retry_wait_seconds += delay_seconds
            sleep(delay_seconds)
            continue
        report.estimated_tokens_sent += batch_tokens
        return vectors
    raise AssertionError("unreachable: the last attempt either returns or raises")


def _require_unique(chunk_ids: list[str]) -> None:
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("Two chunks produced the same chunk ID; refusing to overwrite one")
