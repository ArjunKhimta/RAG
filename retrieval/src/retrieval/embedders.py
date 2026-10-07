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

API failures become `EmbeddingRequestError`, a `GeminiRequestError` (see `gemini_errors.py`)
carrying the HTTP status, any suggested retry delay, and any exceeded quota IDs, so callers never
handle SDK types and can tell a per-minute limit from the daily one. A call cut off by the client's
30-second limit, or a dropped connection, becomes a temporary `EmbeddingRequestError` too, and a
call slower than `SLOW_GEMINI_CALL_SECONDS` (10) is logged.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Protocol

import httpx
from google import genai
from google.genai import errors, types

from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    SLOW_GEMINI_CALL_SECONDS,
)
from retrieval.gemini_errors import (
    GeminiRequestError,
    gemini_request_error,
    transport_request_error,
)

logger = logging.getLogger(__name__)

RETRIEVAL_DOCUMENT_TASK_TYPE = "RETRIEVAL_DOCUMENT"

CODE_RETRIEVAL_QUERY_TASK_TYPE = "CODE_RETRIEVAL_QUERY"


class EmbeddingRequestError(GeminiRequestError):
    """Raised when an embedding or token-count request fails."""


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
        response = _call_gemini(
            "token count",
            lambda: self._client.models.count_tokens(model=self.model_id, contents=text),
        )
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
    response = _call_gemini(
        "embedding",
        lambda: client.models.embed_content(model=model, contents=texts, config=config),
    )
    vectors = [embedding.values for embedding in response.embeddings]
    if len(vectors) != len(texts):
        message = f"Gemini returned {len(vectors)} vectors for {len(texts)} texts"
        raise EmbeddingRequestError(message, status_code=None, retry_after_seconds=None)
    return [normalize_to_unit_length(vector) for vector in vectors]


def _call_gemini[ResultType](
    description: str,
    call: Callable[[], ResultType],
    clock: Callable[[], float] = time.monotonic,
) -> ResultType:
    """Make one Gemini call, turning its failures into `EmbeddingRequestError` and logging it when
    slow."""
    call_started = clock()
    try:
        return call()
    except errors.APIError as error:
        raise gemini_request_error(error, EmbeddingRequestError) from error
    except httpx.TransportError as error:
        raise transport_request_error(
            error, EmbeddingRequestError, GEMINI_REQUEST_TIMEOUT_SECONDS
        ) from error
    finally:
        call_seconds = clock() - call_started
        if call_seconds > SLOW_GEMINI_CALL_SECONDS:
            logger.warning("Gemini %s call took %.1f s", description, call_seconds)
