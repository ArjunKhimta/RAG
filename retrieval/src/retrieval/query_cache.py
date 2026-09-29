"""Cache question embeddings in MongoDB, so asking the same question again costs no request.

`CachingQueryEmbedder` wraps any `QueryEmbedder`. It cleans the question's whitespace, hashes it
with the model, dimensions, and task type, and returns the stored vector if the hash is there.
Otherwise it embeds the cleaned question, stores the vector, and returns it. Search code sees an
ordinary `QueryEmbedder` and never knows the cache exists.

The match is exact. Sharing a vector between merely similar questions would save more requests,
but "how are routes registered" and "how are routes removed" would then retrieve the same code.

A question's vector depends only on the question and the model, never on a repository, so
re-indexing leaves every cached vector valid. Changing the model, dimensions, or task type changes
the key, so an old vector is never reused.

Each document holds the hash, the vector as binary float32, the model, the dimensions, the task
type, and when it was created; never the question itself, so nothing a user typed is kept here. A
TTL index deletes documents 30 days after creation, which bounds the collection's size without a
write on every hit. A vector is returned at float32 precision on a miss as well as a hit, so search
results do not depend on whether the cache was warm.
"""

from __future__ import annotations

from array import array
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from bson.binary import Binary, BinaryVectorDtype
from pymongo import ASCENDING
from pymongo.database import Database

from retrieval.config import QUERY_EMBEDDING_TTL_SECONDS
from retrieval.embedders import EmbeddingIdentity, QueryEmbedder
from retrieval.embedding_inputs import compute_embedding_key, normalize_question

QUERY_EMBEDDINGS_COLLECTION = "query_embeddings"

FLOAT32_TYPE_CODE = "f"


class QueryEmbeddingStore(Protocol):
    def find_embedding(self, embedding_key: str) -> list[float] | None: ...

    def save_embedding(
        self, embedding_key: str, embedder: EmbeddingIdentity, vector: list[float]
    ) -> None: ...


class MongoQueryEmbeddingStore:
    def __init__(
        self, database: Database, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._query_embeddings = database[QUERY_EMBEDDINGS_COLLECTION]
        self._clock = clock

    def ensure_indexes(self) -> None:
        self._query_embeddings.create_index(
            [("created_at", ASCENDING)], expireAfterSeconds=QUERY_EMBEDDING_TTL_SECONDS
        )

    def find_embedding(self, embedding_key: str) -> list[float] | None:
        document = self._query_embeddings.find_one({"_id": embedding_key}, {"embedding": 1})
        if document is None:
            return None
        return vector_from_document(document)

    def save_embedding(
        self, embedding_key: str, embedder: EmbeddingIdentity, vector: list[float]
    ) -> None:
        document = query_embedding_document(embedding_key, embedder, vector, self._clock())
        self._query_embeddings.replace_one({"_id": embedding_key}, document, upsert=True)


class CachingQueryEmbedder:
    def __init__(self, embedder: QueryEmbedder, store: QueryEmbeddingStore) -> None:
        self._embedder = embedder
        self._store = store
        self.model_id = embedder.model_id
        self.dimensions = embedder.dimensions
        self.task_type = embedder.task_type
        self.hit_count = 0
        self.miss_count = 0

    def embed_query(self, question: str) -> list[float]:
        cleaned_question = normalize_question(question)
        embedding_key = compute_embedding_key(self._embedder, cleaned_question)
        cached_vector = self._store.find_embedding(embedding_key)
        if cached_vector is not None:
            self.hit_count += 1
            return cached_vector
        vector = round_to_float32(self._embedder.embed_query(cleaned_question))
        self._store.save_embedding(embedding_key, self._embedder, vector)
        self.miss_count += 1
        return vector


def query_embedding_document(
    embedding_key: str, embedder: EmbeddingIdentity, vector: list[float], created_at: datetime
) -> dict[str, Any]:
    return {
        "_id": embedding_key,
        "embedding": Binary.from_vector(vector, BinaryVectorDtype.FLOAT32),
        "model": embedder.model_id,
        "dimensions": embedder.dimensions,
        "task_type": embedder.task_type,
        "created_at": created_at,
    }


def vector_from_document(document: dict[str, Any]) -> list[float]:
    return list(document["embedding"].as_vector().data)


def round_to_float32(vector: list[float]) -> list[float]:
    return array(FLOAT32_TYPE_CODE, vector).tolist()
