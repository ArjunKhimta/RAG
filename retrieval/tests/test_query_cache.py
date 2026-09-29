from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bson import BSON
from bson.binary import Binary, BinaryVectorDtype

from retrieval.config import QUERY_EMBEDDING_TTL_SECONDS
from retrieval.embedding_inputs import compute_embedding_key
from retrieval.query_cache import (
    QUERY_EMBEDDINGS_COLLECTION,
    CachingQueryEmbedder,
    MongoQueryEmbeddingStore,
    query_embedding_document,
    round_to_float32,
    vector_from_document,
)

CREATED_AT = datetime(2026, 9, 29, tzinfo=UTC)

VECTOR = [0.1, 0.2, 0.3]


class FakeQueryEmbedder:
    def __init__(self, model_id="gemini-embedding-001", dimensions=3):
        self.model_id = model_id
        self.dimensions = dimensions
        self.task_type = "CODE_RETRIEVAL_QUERY"
        self.embedded_questions = []

    def embed_query(self, question):
        self.embedded_questions.append(question)
        return VECTOR


class FakeQueryEmbeddingStore:
    def __init__(self):
        self.vectors_by_key = {}

    def find_embedding(self, embedding_key):
        return self.vectors_by_key.get(embedding_key)

    def save_embedding(self, embedding_key, embedder, vector):
        self.vectors_by_key[embedding_key] = vector


class FakeCollection:
    def __init__(self):
        self.created_indexes = []

    def create_index(self, keys, **options):
        self.created_indexes.append((keys, options))


def test_a_new_question_is_embedded_once_and_stored():
    embedder = FakeQueryEmbedder()
    store = FakeQueryEmbeddingStore()
    caching_embedder = CachingQueryEmbedder(embedder, store)

    vector = caching_embedder.embed_query("How are routes registered?")

    assert embedder.embedded_questions == ["How are routes registered?"]
    assert list(store.vectors_by_key.values()) == [vector]
    assert (caching_embedder.hit_count, caching_embedder.miss_count) == (0, 1)


def test_a_repeated_question_is_served_from_the_cache_without_embedding():
    embedder = FakeQueryEmbedder()
    caching_embedder = CachingQueryEmbedder(embedder, FakeQueryEmbeddingStore())

    first_vector = caching_embedder.embed_query("How are routes registered?")
    second_vector = caching_embedder.embed_query("How are routes registered?")

    assert len(embedder.embedded_questions) == 1
    assert second_vector == first_vector
    assert (caching_embedder.hit_count, caching_embedder.miss_count) == (1, 1)


def test_extra_spaces_and_newlines_reuse_the_same_entry_and_the_cleaned_text_is_embedded():
    embedder = FakeQueryEmbedder()
    caching_embedder = CachingQueryEmbedder(embedder, FakeQueryEmbeddingStore())

    caching_embedder.embed_query("  How are   routes\nregistered? ")
    caching_embedder.embed_query("How are routes registered?")

    assert embedder.embedded_questions == ["How are routes registered?"]
    assert caching_embedder.hit_count == 1


def test_questions_differing_only_in_case_are_cached_separately():
    embedder = FakeQueryEmbedder()
    caching_embedder = CachingQueryEmbedder(embedder, FakeQueryEmbeddingStore())

    caching_embedder.embed_query("What does Flask.run do?")
    caching_embedder.embed_query("what does flask.run do?")

    assert len(embedder.embedded_questions) == 2


def test_a_different_model_does_not_reuse_another_models_vector():
    store = FakeQueryEmbeddingStore()
    first_embedder = FakeQueryEmbedder(model_id="model-a")
    second_embedder = FakeQueryEmbedder(model_id="model-b")

    CachingQueryEmbedder(first_embedder, store).embed_query("question")
    CachingQueryEmbedder(second_embedder, store).embed_query("question")

    assert second_embedder.embedded_questions == ["question"]
    assert len(store.vectors_by_key) == 2


def test_the_same_text_as_a_question_and_as_a_document_has_different_keys():
    query_identity = FakeQueryEmbedder()
    document_identity = SimpleNamespace(
        model_id=query_identity.model_id,
        dimensions=query_identity.dimensions,
        task_type="RETRIEVAL_DOCUMENT",
    )

    query_key = compute_embedding_key(query_identity, "def run(self):")
    document_key = compute_embedding_key(document_identity, "def run(self):")

    assert query_key != document_key


def test_a_miss_returns_the_same_float32_vector_a_later_hit_returns():
    embedder = FakeQueryEmbedder()
    caching_embedder = CachingQueryEmbedder(embedder, FakeQueryEmbeddingStore())

    missed_vector = caching_embedder.embed_query("question")

    assert missed_vector == round_to_float32(VECTOR)
    assert missed_vector != VECTOR


def test_the_caching_embedder_reports_the_wrapped_embedders_identity():
    caching_embedder = CachingQueryEmbedder(FakeQueryEmbedder(), FakeQueryEmbeddingStore())

    assert caching_embedder.model_id == "gemini-embedding-001"
    assert caching_embedder.dimensions == 3
    assert caching_embedder.task_type == "CODE_RETRIEVAL_QUERY"


def test_a_stored_document_holds_the_vector_and_identity_but_not_the_question():
    document = query_embedding_document("key-1", FakeQueryEmbedder(), VECTOR, CREATED_AT)

    assert set(document) == {"_id", "embedding", "model", "dimensions", "task_type", "created_at"}
    assert document["_id"] == "key-1"
    assert isinstance(document["embedding"], Binary)
    assert document["embedding"].as_vector().dtype == BinaryVectorDtype.FLOAT32
    assert document["created_at"] == CREATED_AT


def test_a_vector_survives_a_round_trip_through_bson():
    document = query_embedding_document("key-1", FakeQueryEmbedder(), VECTOR, CREATED_AT)

    decoded_document = BSON(BSON.encode(document)).decode()

    assert vector_from_document(decoded_document) == pytest.approx(VECTOR)
    assert vector_from_document(decoded_document) == round_to_float32(VECTOR)


def test_documents_expire_thirty_days_after_creation():
    collection = FakeCollection()
    store = MongoQueryEmbeddingStore({QUERY_EMBEDDINGS_COLLECTION: collection})

    store.ensure_indexes()

    keys, options = collection.created_indexes[0]
    assert keys == [("created_at", 1)]
    assert options == {"expireAfterSeconds": QUERY_EMBEDDING_TTL_SECONDS}
    assert QUERY_EMBEDDING_TTL_SECONDS == 30 * 24 * 60 * 60
