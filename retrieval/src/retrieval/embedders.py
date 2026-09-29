"""Embedders: turn chunk texts and search questions into vectors.

`DocumentEmbedder` and `QueryEmbedder` are the small interfaces the indexer and search depend on,
so a local model served by Ollama can replace Gemini later without touching caching, storage, or
search. Every embedder returns vectors of length 1, so cosine similarity and dot product give the
same ranking.

Documents and questions use different task types. `GeminiDocumentEmbedder` embeds chunks with
`RETRIEVAL_DOCUMENT`, and `GeminiQueryEmbedder` embeds questions with `CODE_RETRIEVAL_QUERY`. The
model was trained on these as a pair, so a plain-English question lands near the code that answers
it rather than near code that merely uses the same words. Gemini returns 768-dimension vectors
unnormalized (only the full 3,072 are normalized), so they are normalized here.

API failures become `EmbeddingRequestError`, carrying the HTTP status, any retry delay the API
suggests, and the IDs of any quotas that were exceeded, so callers never handle SDK types.

A 429 can mean the per-minute limits, which free up within a minute, or the daily limit, which
does not free up until the daily reset. The quota IDs tell them apart: daily ones contain
`PerDay`, as in `EmbedContentRequestsPerDayPerProjectPerModel-FreeTier`. A daily-quota error is
never worth retrying.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from google import genai
from google.genai import errors, types

from retrieval.config import EMBEDDING_DIMENSIONS, GEMINI_EMBEDDING_MODEL

RETRIEVAL_DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"

CODE_RETRIEVAL_QUERY_TASK_TYPE = "CODE_RETRIEVAL_QUERY"

RETRYABLE_STATUS_CODES = frozenset({429, 500, 503})

RETRY_INFO_TYPE_SUFFIX = "google.rpc.RetryInfo"

QUOTA_FAILURE_TYPE_SUFFIX = "google.rpc.QuotaFailure"

DAILY_QUOTA_ID_MARKER = "PerDay"

SECONDS_SUFFIX = "s"


class EmbeddingRequestError(RuntimeError):
    """Raised when an embedding or token-count request fails."""

    def __init__(
        self,
        message: str,
        status_code: int | None,
        retry_after_seconds: float | None,
        exceeded_quota_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.exceeded_quota_ids = exceeded_quota_ids

    @property
    def is_daily_quota_exhausted(self) -> bool:
        return any(DAILY_QUOTA_ID_MARKER in quota_id for quota_id in self.exceeded_quota_ids)

    @property
    def is_retryable(self) -> bool:
        return self.status_code in RETRYABLE_STATUS_CODES and not self.is_daily_quota_exhausted


class EmbeddingIdentity(Protocol):
    """Everything besides the input text that determines a vector, and so belongs in a cache key."""

    model_id: str
    dimensions: int
    task_type: str


class DocumentEmbedder(EmbeddingIdentity, Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one unit-length vector per text, in order."""
        ...

    def count_tokens(self, text: str) -> int:
        """Return the exact number of tokens the model sees for `text`."""
        ...


class QueryEmbedder(EmbeddingIdentity, Protocol):
    def embed_query(self, question: str) -> list[float]:
        """Return one unit-length vector for a search question."""
        ...


class GeminiDocumentEmbedder:
    def __init__(
        self,
        client: genai.Client,
        model: str = GEMINI_EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self._client = client
        self.model_id = model
        self.dimensions = dimensions
        self.task_type = RETRIEVAL_DOCUMENT_TASK_TYPE

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return _embed_texts(self._client, self.model_id, self.dimensions, self.task_type, texts)

    def count_tokens(self, text: str) -> int:
        try:
            response = self._client.models.count_tokens(model=self.model_id, contents=text)
        except errors.APIError as error:
            raise _embedding_request_error(error) from error
        return response.total_tokens


class GeminiQueryEmbedder:
    def __init__(
        self,
        client: genai.Client,
        model: str = GEMINI_EMBEDDING_MODEL,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self._client = client
        self.model_id = model
        self.dimensions = dimensions
        self.task_type = CODE_RETRIEVAL_QUERY_TASK_TYPE

    def embed_query(self, question: str) -> list[float]:
        vectors = _embed_texts(
            self._client, self.model_id, self.dimensions, self.task_type, [question]
        )
        return vectors[0]


def normalize_to_unit_length(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length == 0:
        raise ValueError("A zero vector cannot be normalized")
    return [value / length for value in vector]


def _embed_texts(
    client: genai.Client, model: str, dimensions: int, task_type: str, texts: list[str]
) -> list[list[float]]:
    config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=dimensions)
    try:
        response = client.models.embed_content(model=model, contents=texts, config=config)
    except errors.APIError as error:
        raise _embedding_request_error(error) from error
    vectors = [embedding.values for embedding in response.embeddings]
    if len(vectors) != len(texts):
        message = f"Gemini returned {len(vectors)} vectors for {len(texts)} texts"
        raise EmbeddingRequestError(message, status_code=None, retry_after_seconds=None)
    return [normalize_to_unit_length(vector) for vector in vectors]


def _embedding_request_error(error: errors.APIError) -> EmbeddingRequestError:
    exceeded_quota_ids = _exceeded_quota_ids(error.details)
    message = f"Gemini request failed with HTTP {error.code} {error.status}: {error.message}"
    if exceeded_quota_ids:
        message += f" [quota: {', '.join(exceeded_quota_ids)}]"
    return EmbeddingRequestError(
        message,
        status_code=error.code,
        retry_after_seconds=_suggested_retry_seconds(error.details),
        exceeded_quota_ids=exceeded_quota_ids,
    )


def _error_details(response_json: Any) -> list[dict[str, Any]]:
    if not isinstance(response_json, dict):
        return []
    error_json = response_json.get("error", response_json)
    details = error_json.get("details") or []
    return [detail for detail in details if isinstance(detail, dict)]


def _exceeded_quota_ids(response_json: Any) -> tuple[str, ...]:
    """Read quota IDs from `google.rpc.QuotaFailure` entries in the error response."""
    quota_ids: list[str] = []
    for detail in _error_details(response_json):
        if not str(detail.get("@type", "")).endswith(QUOTA_FAILURE_TYPE_SUFFIX):
            continue
        for violation in detail.get("violations") or []:
            quota_id = violation.get("quotaId") if isinstance(violation, dict) else None
            if isinstance(quota_id, str):
                quota_ids.append(quota_id)
    return tuple(quota_ids)


def _suggested_retry_seconds(response_json: Any) -> float | None:
    """Read the delay from a `google.rpc.RetryInfo` entry, such as `"retryDelay": "17s"`."""
    for detail in _error_details(response_json):
        is_retry_info = str(detail.get("@type", "")).endswith(RETRY_INFO_TYPE_SUFFIX)
        retry_delay = detail.get("retryDelay")
        if is_retry_info and isinstance(retry_delay, str):
            try:
                return float(retry_delay.removesuffix(SECONDS_SUFFIX))
            except ValueError:
                return None
    return None
