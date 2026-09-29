from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from google.genai import errors

from retrieval.embedders import (
    EmbeddingRequestError,
    GeminiDocumentEmbedder,
    GeminiQueryEmbedder,
    normalize_to_unit_length,
)


class FakeModels:
    def __init__(self, vectors=None, error=None, total_tokens=0):
        self.vectors = vectors or []
        self.error = error
        self.total_tokens = total_tokens
        self.embed_calls = []

    def embed_content(self, model, contents, config):
        self.embed_calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        embeddings = [SimpleNamespace(values=vector) for vector in self.vectors]
        return SimpleNamespace(embeddings=embeddings)

    def count_tokens(self, model, contents):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(total_tokens=self.total_tokens)


def _embedder(models: FakeModels) -> GeminiDocumentEmbedder:
    return GeminiDocumentEmbedder(SimpleNamespace(models=models), model="model-a", dimensions=2)


def test_documents_are_embedded_with_the_document_task_type_and_dimensions():
    models = FakeModels(vectors=[[3.0, 4.0], [0.0, 2.0]])

    vectors = _embedder(models).embed_documents(["first", "second"])

    call = models.embed_calls[0]
    assert call["model"] == "model-a"
    assert call["contents"] == ["first", "second"]
    assert call["config"].task_type == "RETRIEVAL_DOCUMENT"
    assert call["config"].output_dimensionality == 2
    assert vectors == [[0.6, 0.8], [0.0, 1.0]]


def test_a_question_is_embedded_alone_with_the_code_retrieval_query_task_type():
    models = FakeModels(vectors=[[3.0, 4.0]])
    embedder = GeminiQueryEmbedder(SimpleNamespace(models=models), model="model-a", dimensions=2)

    vector = embedder.embed_query("How are routes registered?")

    call = models.embed_calls[0]
    assert call["model"] == "model-a"
    assert call["contents"] == ["How are routes registered?"]
    assert call["config"].task_type == "CODE_RETRIEVAL_QUERY"
    assert call["config"].output_dimensionality == 2
    assert vector == [0.6, 0.8]


def test_a_query_embedding_failure_becomes_an_embedding_request_error():
    bad_request_json = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Bad"}}
    models = FakeModels(error=errors.ClientError(400, bad_request_json))
    embedder = GeminiQueryEmbedder(SimpleNamespace(models=models))

    with pytest.raises(EmbeddingRequestError) as raised:
        embedder.embed_query("question")

    assert raised.value.status_code == 400


def test_a_vector_count_mismatch_is_an_error():
    models = FakeModels(vectors=[[1.0, 0.0]])

    with pytest.raises(EmbeddingRequestError):
        _embedder(models).embed_documents(["first", "second"])


def test_a_rate_limit_error_carries_the_suggested_retry_delay():
    rate_limit_json = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}
            ],
        }
    }
    models = FakeModels(error=errors.ClientError(429, rate_limit_json))

    with pytest.raises(EmbeddingRequestError) as raised:
        _embedder(models).embed_documents(["text"])

    assert raised.value.status_code == 429
    assert raised.value.retry_after_seconds == 17.0
    assert raised.value.is_retryable


def test_an_exhausted_daily_quota_is_recognised_and_not_retryable():
    daily_quota_json = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {
                            "quotaMetric": "generativelanguage.googleapis.com/"
                            "embed_content_free_tier_requests",
                            "quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier",
                        }
                    ],
                },
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "24s"},
            ],
        }
    }
    models = FakeModels(error=errors.ClientError(429, daily_quota_json))

    with pytest.raises(EmbeddingRequestError) as raised:
        _embedder(models).embed_documents(["text"])

    assert raised.value.is_daily_quota_exhausted
    assert not raised.value.is_retryable
    assert "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier" in str(raised.value)


def test_an_exhausted_per_minute_quota_is_still_retryable():
    minute_quota_json = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [
                        {"quotaId": "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier"}
                    ],
                }
            ],
        }
    }
    models = FakeModels(error=errors.ClientError(429, minute_quota_json))

    with pytest.raises(EmbeddingRequestError) as raised:
        _embedder(models).embed_documents(["text"])

    assert not raised.value.is_daily_quota_exhausted
    assert raised.value.is_retryable


def test_a_bad_request_is_not_retryable_and_has_no_delay():
    bad_request_json = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "Bad"}}
    models = FakeModels(error=errors.ClientError(400, bad_request_json))

    with pytest.raises(EmbeddingRequestError) as raised:
        _embedder(models).embed_documents(["text"])

    assert raised.value.status_code == 400
    assert raised.value.retry_after_seconds is None
    assert not raised.value.is_retryable


def test_tokens_are_counted_with_the_same_model():
    models = FakeModels(total_tokens=1234)

    assert _embedder(models).count_tokens("text") == 1234


def test_normalized_vectors_have_length_one():
    normalized_vector = normalize_to_unit_length([1.0, 2.0, 2.0])

    assert math.sqrt(sum(value * value for value in normalized_vector)) == pytest.approx(1.0)


def test_a_zero_vector_cannot_be_normalized():
    with pytest.raises(ValueError):
        normalize_to_unit_length([0.0, 0.0])
